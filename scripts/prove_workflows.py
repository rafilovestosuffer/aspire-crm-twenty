#!/usr/bin/env python3
"""
Run every workflow against the live stack and assert what it actually did.

The difference between "deployed" and "working" is the whole risk in this
migration. Deploying is easy and proves nothing: for weeks these workflows sat
in n8n, structurally valid and completely broken — filters that Twenty silently
ignored, an error workflow bound to a name that resolved to nothing, consent
records created with no person attached. Every one of those looked fine in the
editor.

So this does not check that workflows exist. It submits the real form, follows
the real redirect, triggers the real schedules, and then asks Twenty whether the
records it was supposed to create are there.

Requires the dev profile: Mailpit catches the mail, the alert sink catches the
chat webhook. Nothing leaves the host.

    ./infra/up.sh
    python scripts/n8n_credentials.py
    python scripts/n8n_deploy.py --dev
    python scripts/prove_workflows.py

Usage:
    python scripts/prove_workflows.py
    python scripts/prove_workflows.py --only consent
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parent.parent

GREEN, RED, DIM, BOLD, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for f in (ROOT / "infra" / ".env", ROOT / ".env"):
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env.setdefault(k.strip(), v.strip().strip("\"'"))
    for k in ("TWENTY_BASE_URL", "TWENTY_API_KEY", "N8N_BASE_URL", "N8N_API_KEY"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


ENV = read_env()
TWENTY = ENV.get("TWENTY_BASE_URL", "http://localhost:3000")
TWENTY_KEY = ENV.get("TWENTY_API_KEY", "")
N8N = ENV.get("N8N_BASE_URL", "http://localhost:5678")
N8N_KEY = ENV.get("N8N_API_KEY", "")
MAILPIT = ENV.get("MAILPIT_URL", "http://localhost:8025")


def http(method: str, url: str, *, headers=None, data=None,
         redirect=True) -> tuple[int, bytes, dict]:
    class NoRedirect(request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    req = request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    opener = request.build_opener() if redirect else request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=60) as r:
            return r.status, r.read(), dict(r.headers)
    except error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def twenty(path: str) -> dict:
    status, body, _ = http("GET", f"{TWENTY}{path}",
                           headers={"Authorization": f"Bearer {TWENTY_KEY}"})
    if status >= 400:
        raise RuntimeError(f"Twenty {status}: {body[:200].decode('utf-8','replace')}")
    return json.loads(body or "{}")


def twenty_post(obj: str, body: dict) -> dict:
    status, raw, _ = http("POST", f"{TWENTY}/rest/{obj}",
                          headers={"Authorization": f"Bearer {TWENTY_KEY}",
                                   "Content-Type": "application/json"},
                          data=json.dumps(body).encode())
    if status >= 400:
        raise RuntimeError(f"Twenty {status}: {raw[:200].decode('utf-8','replace')}")
    d = json.loads(raw or "{}").get("data", {})
    return next(iter(d.values()), {}) if d else {}


def rows(obj: str, query: str = "") -> list[dict]:
    parts = [q for q in query.split("&") if q]
    if not any(q.startswith("limit=") for q in parts):
        parts.append("limit=200")
    d = twenty(f"/rest/{obj}?" + "&".join(parts))
    return d.get("data", {}).get(obj, [])


class N8nSession:
    """
    Manual runs go through the internal API — the public one cannot start a
    workflow. Cookies are sent as an explicit header because http.cookiejar
    refuses to send them back to a bare `localhost`.
    """

    def __init__(self):
        self.token = ""

    def login(self, email: str, password: str) -> bool:
        status, _, headers = http(
            "POST", f"{N8N}/rest/login",
            headers={"Content-Type": "application/json", "browser-id": "prove"},
            data=json.dumps({"emailOrLdapLoginId": email,
                             "password": password}).encode())
        raw = headers.get("Set-Cookie", "")
        for part in raw.split(","):
            if "n8n-auth=" in part:
                self.token = part.split("n8n-auth=", 1)[1].split(";")[0]
        return status == 200 and bool(self.token)

    def _h(self) -> dict:
        return {"Content-Type": "application/json", "browser-id": "prove",
                "Cookie": f"n8n-auth={self.token}"}

    def workflow(self, wid: str) -> dict:
        status, body, _ = http("GET", f"{N8N}/rest/workflows/{wid}",
                               headers=self._h())
        return json.loads(body or "{}").get("data", {})

    def run(self, wid: str, trigger: str) -> str:
        """Start a manual run; returns the execution id."""
        wf = self.workflow(wid)
        payload = {
            "workflowData": wf,
            "triggerToStartFrom": {"name": trigger},
        }
        status, body, _ = http("POST", f"{N8N}/rest/workflows/{wid}/run",
                               headers=self._h(), data=json.dumps(payload).encode())
        d = json.loads(body or "{}")
        return (d.get("data") or {}).get("executionId", "")

    def wait(self, execution_id: str, timeout: int = 90) -> dict:
        """
        Poll the PUBLIC api for the result, not the internal one.

        The internal endpoint returns run data in `flatted` form — a flat array
        with numeric back-references, not the nested object it looks like — so
        reading runData from it silently yields the wrong shape.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            status, body, _ = http(
                "GET", f"{N8N}/api/v1/executions/{execution_id}?includeData=true",
                headers={"X-N8N-API-KEY": N8N_KEY})
            if status < 400:
                d = json.loads(body or "{}")
                if d.get("finished") or d.get("status") in (
                        "success", "error", "crashed"):
                    return d
            time.sleep(1)
        return {}


def api_workflows() -> dict[str, str]:
    status, body, _ = http("GET", f"{N8N}/api/v1/workflows?limit=100",
                           headers={"X-N8N-API-KEY": N8N_KEY})
    if status >= 400:
        raise RuntimeError(f"n8n {status}")
    return {w["name"]: w["id"] for w in json.loads(body)["data"]}


def mailpit_clear() -> None:
    http("DELETE", f"{MAILPIT}/api/v1/messages")


def mailpit_messages() -> list[dict]:
    status, body, _ = http("GET", f"{MAILPIT}/api/v1/messages?limit=50")
    if status >= 400:
        return []
    return json.loads(body or "{}").get("messages", [])


def submit_form(fields: list[str]) -> int:
    """
    n8n names form inputs `field-0`, `field-1`, ... by position, not by label.
    Posting under the labels returns 200 with every value silently null.
    """
    boundary = "----aspireproof"
    parts = []
    for i, value in enumerate(fields):
        parts.append(f"--{boundary}\r\n"
                     f'Content-Disposition: form-data; name="field-{i}"\r\n\r\n'
                     f"{value}\r\n")
    parts.append(f"--{boundary}--\r\n")
    data = "".join(parts).encode()
    status, _, _ = http("POST", f"{N8N}/form/aspire-contact", data=data,
                        headers={"Content-Type":
                                 f"multipart/form-data; boundary={boundary}"})
    return status


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

RESULTS: list[tuple[str, bool, str]] = []

# Records this run created, newest first, as (object, id). Cleared up at the
# end unless --keep: the proof runs against the same instance that gets
# demonstrated, and "Proof Lead 2b5a508b" sitting in the pipeline during a
# walkthrough undoes the point of seeding realistic data in the first place.
CREATED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    mark = f"{GREEN}PASS{OFF}" if ok else f"{RED}FAIL{OFF}"
    print(f"  {mark}  {name}" + (f"  {DIM}{detail}{OFF}" if detail else ""))
    return ok


def prove_lead_form() -> None:
    print(f"\n{BOLD}LEAD Form Intake — public form to CRM record{OFF}")
    tag = uuid.uuid4().hex[:8]
    email = f"proof.{tag}@northgate-{tag}.com"
    mailpit_clear()

    status = submit_form(["Proof", f"Lead{tag}", email, f"Northgate {tag}",
                          "555-0100", "SOC / managed detection",
                          "Automated proof run.", "true"])
    if not check("form accepts submission", status == 200, f"HTTP {status}"):
        return
    time.sleep(6)

    people = rows("people", f"filter=emails.primaryEmail[eq]:{parse.quote(email)}")
    if not check("person created", len(people) == 1, f"{len(people)} found"):
        return
    person = people[0]
    CREATED.append(("people", person["id"]))
    if person.get("companyId"):
        CREATED.append(("companies", person["companyId"]))

    check("company created and linked", bool(person.get("companyId")),
          str(person.get("companyId")))

    consent = rows("consentRecords",
                   f"filter=personId[eq]:{person['id']}&order_by=createdAt[DescNullsLast]")
    check("consent record linked to the person", len(consent) >= 1)
    check("consent recorded as OPTED_IN",
          bool(consent) and consent[0].get("status") == "OPTED_IN",
          consent[0].get("status") if consent else "none")

    tasks = rows("tasks", "order_by=createdAt[DescNullsLast]")
    hot = [t for t in tasks if tag in (t.get("title") or "")]
    check("follow-up task raised with score", bool(hot),
          hot[0]["title"] if hot else "none")

    mail = mailpit_messages()
    to = mail[0]["To"][0]["Address"] if mail else ""
    check("acknowledgement sent to the right address", to == email, to or "no mail")


def prove_consent_gate() -> None:
    print(f"\n{BOLD}Consent gate — the send must be refused{OFF}")
    tag = uuid.uuid4().hex[:8]
    email = f"noconsent.{tag}@bluffpoint-{tag}.com"
    mailpit_clear()

    status = submit_form(["NoConsent", f"Test{tag}", email, f"Bluffpoint {tag}",
                          "555-0101", "Incident response",
                          "Consent gate proof.", "false"])
    if not check("form accepts submission", status == 200, f"HTTP {status}"):
        return
    time.sleep(6)

    people = rows("people", f"filter=emails.primaryEmail[eq]:{parse.quote(email)}")
    if not check("person still created", len(people) == 1):
        return
    CREATED.append(("people", people[0]["id"]))
    if people[0].get("companyId"):
        CREATED.append(("companies", people[0]["companyId"]))

    consent = rows("consentRecords", f"filter=personId[eq]:{people[0]['id']}")
    check("consent recorded as PENDING, not OPTED_IN",
          bool(consent) and consent[0].get("status") == "PENDING",
          consent[0].get("status") if consent else "none")

    check("NO email was sent", len(mailpit_messages()) == 0,
          f"{len(mailpit_messages())} message(s)")

    logs = rows("messageLogs", "order_by=createdAt[DescNullsLast]")
    blocked = [m for m in logs[:5]
               if (m.get("subject") or "").startswith("Blocked:")]
    check("refusal logged in the CRM with a reason", bool(blocked),
          blocked[0]["subject"] if blocked else "none")


def prove_tracked_link() -> None:
    print(f"\n{BOLD}MSG Tracked Link Redirect — click tracking{OFF}")
    # Create the link rather than borrowing a seeded one. A check that only
    # runs when the demo data happens to contain the right record is a check
    # that quietly stops running — this one did, on the first clean rebuild.
    slug = "proof-" + uuid.uuid4().hex[:8]
    try:
        link = twenty_post("trackedLinks", {
            "name": f"Proof link {slug}",
            "slug": slug,
            "destinationUrl": {"primaryLinkUrl": "https://aspiretss.com/cmmc"},
            "clickCount": 0,
        })
    except RuntimeError as e:
        check("a tracked link can be created to test", False, str(e)[:90])
        return
    CREATED.append(("trackedLinks", link["id"]))
    check("a tracked link exists to test", bool(link.get("id")), slug)
    before = link.get("clickCount") or 0

    status, _, headers = http("GET", f"{N8N}/webhook/t?s={link['slug']}",
                              redirect=False)
    # Header names are case-insensitive on the wire; a plain dict is not.
    target = next((v for k, v in headers.items() if k.lower() == "location"), "")
    check("click redirects to the destination",
          status in (301, 302, 307, 308) and bool(target),
          f"HTTP {status} -> {target}")
    time.sleep(3)

    after = (rows("trackedLinks", f"filter=slug[eq]:{link['slug']}")[0]
             .get("clickCount") or 0)
    check("click counted in Twenty", after == before + 1, f"{before} -> {after}")


def prove_error_handler(sess: N8nSession, ids: dict) -> None:
    """
    Break something on purpose and check the safety net catches it.

    Asserting that the error handler *exists* proves nothing — on a freshly
    built stack nothing has failed, so it has no executions and no evidence
    either way. `SYS Failure Probe (dev)` throws deliberately so the real path
    runs: error trigger, automationRun FAILED, alert.
    """
    print(f"\n{BOLD}SYS Error Handler — failures are caught and alerted{OFF}")

    status, _, _ = http("GET", f"{N8N}/webhook/t?s=__definitely_not_a_slug__",
                        redirect=False)
    check("unknown slug does not 5xx", status < 500, f"HTTP {status}")

    before = len(rows("automationRuns", "filter=status[eq]:FAILED"))
    alerts_before = len([r for r in rows("automationRuns")
                         if (r.get("workflowName") or "").startswith("ALERT")])

    have_probe = bool(ids.get("SYS Failure Probe (dev)"))
    if not check("failure probe deployed", have_probe,
                 "" if have_probe else "deploy with --dev --activate"):
        return
    # Hit the production webhook. A manual run would fail in the editor without
    # ever invoking the error workflow, which is the thing under test.
    # The webhook answers on receipt, before the workflow runs, so a 200 here
    # says nothing about the outcome. The proof is the automationRun below.
    status, _, _ = http("GET", f"{N8N}/webhook/fail-probe", redirect=False)
    check("failure probe fired", status < 400, f"HTTP {status}")

    # The error workflow runs after the failing execution finishes.
    failed, alerts = [], []
    for _ in range(15):
        time.sleep(2)
        failed = rows("automationRuns", "filter=status[eq]:FAILED"
                                        "&order_by=createdAt[DescNullsLast]")
        alerts = [r for r in rows("automationRuns")
                  if (r.get("workflowName") or "").startswith("ALERT")]
        if len(failed) > before and len(alerts) > alerts_before:
            break

    check("failure recorded in Twenty as automationRun FAILED",
          len(failed) > before,
          failed[0].get("workflowName", "") if failed else "none")
    check("failure message captured, not swallowed",
          bool(failed) and "deliberate failure probe" in
          (failed[0].get("errorMessage") or ""),
          (failed[0].get("errorMessage") or "")[:60] if failed else "")
    check("alert delivered for the failure", len(alerts) > alerts_before,
          f"{len(alerts) - alerts_before} new alert(s)")


def prove_inbound_events() -> None:
    """The consent loop, both halves.

    Recording an opt-out is only half a control; the half that matters is
    whether a later send actually honours it. So this opts a person IN, proves
    the acknowledgement really sends, posts an unsubscribe the way a mail
    provider would, then submits the SAME form again and proves the second
    acknowledgement is refused. Anything less tests the write and assumes the
    read.
    """
    print(f"\n{BOLD}MSG Inbound Events — the consent loop closes{OFF}")
    tag = uuid.uuid4().hex[:8]
    email = f"loop.{tag}@ridgeline-{tag}.com"
    fields = ["Loop", f"Test{tag}", email, f"Ridgeline {tag}", "555-0144",
              "CMMC or compliance", "Consent loop proof.", "true"]

    # --- 1. opted in: the acknowledgement must actually send -------------
    mailpit_clear()
    if not check("form accepts submission", submit_form(fields) == 200):
        return
    time.sleep(7)

    people = rows("people", f"filter=emails.primaryEmail[eq]:{parse.quote(email)}")
    if not check("person created", len(people) == 1):
        return
    pid = people[0]["id"]
    CREATED.append(("people", pid))
    if people[0].get("companyId"):
        CREATED.append(("companies", people[0]["companyId"]))

    if not check("acknowledgement sent while opted in",
                 len(mailpit_messages()) >= 1, f"{len(mailpit_messages())} message(s)"):
        return

    # --- 2. the mail provider reports an unsubscribe ---------------------
    posted, _, _ = http("POST", f"{N8N}/webhook/mail-event",
                        headers={"Content-Type": "application/json"},
                        data=json.dumps({"type": "unsubscribe", "email": email,
                                         "messageId": f"msg-{tag}",
                                         "reason": "clicked the unsubscribe link"}).encode())
    if not check("unsubscribe event accepted", posted == 200, f"HTTP {posted}"):
        return
    time.sleep(7)

    consent = rows("consentRecords",
                   f"filter=personId[eq]:{pid}&order_by=effectiveAt[DescNullsLast]")
    latest = consent[0] if consent else {}
    check("consent flipped to OPTED_OUT", latest.get("status") == "OPTED_OUT",
          latest.get("status") or "none")
    check("source recorded as the unsubscribe link",
          latest.get("source") == "UNSUBSCRIBE_LINK", latest.get("source") or "none")
    check("raw event kept as proof", bool(latest.get("proof")),
          (latest.get("proof") or "")[:48])

    # --- 3. the same form again: this time it must be refused ------------
    mailpit_clear()
    if not check("second submission accepted", submit_form(fields) == 200):
        return
    time.sleep(7)

    check("NO second email sent after opting out",
          len(mailpit_messages()) == 0, f"{len(mailpit_messages())} message(s)")

    logs = rows("messageLogs", f"filter=personId[eq]:{pid}"
                               "&order_by=createdAt[DescNullsLast]")
    blocked = [m for m in logs[:5] if (m.get("subject") or "").startswith("Blocked:")]
    check("refusal logged against the person", bool(blocked),
          blocked[0]["subject"] if blocked else "none")


def prove_scheduled(sess: N8nSession, ids: dict) -> None:
    print(f"\n{BOLD}Scheduled workflows — renewals and sweeps{OFF}")
    for name, trigger in (("SUB Renewal Escalation", "Every morning"),
                          ("OPS Scheduled Sweeps", "Every morning"),
                          ("LEAD Nurture Sequence", "Every morning")):
        wid = ids.get(name)
        if not wid:
            check(f"{name} deployed", False, "not found")
            continue
        wf = sess.workflow(wid)
        trig = next((n["name"] for n in wf.get("nodes", [])
                     if "schedule" in n.get("type", "").lower()), trigger)
        exec_id = sess.run(wid, trig)
        if not check(f"{name} starts", bool(exec_id)):
            continue
        result = sess.wait(exec_id)
        status = result.get("status", "unknown")
        failed = [
            (n, r["error"].get("message", "")[:90])
            for n, runs_ in ((result.get("data") or {})
                             .get("resultData", {}).get("runData", {}) or {}).items()
            for r in runs_ if r.get("error")
        ]
        check(f"{name} completes without node errors",
              status == "success" and not failed,
              failed[0][0] + ": " + failed[0][1] if failed else status)


def cleanup() -> None:
    """
    Remove the records this run created.

    Deletes people and companies only. The consent records, message logs and
    automationRuns it also produced are the audit trail — they are supposed to
    outlive the thing they describe, and a run that erased its own suppression
    evidence would be a strange thing to ship.
    """
    if not CREATED:
        return
    gone = 0
    for obj, rid in reversed(CREATED):
        # A proof run finishes right after a burst of writes, so the deletes
        # land on a saturated rate limit and 429. Without a retry the cleanup
        # quietly gave up and left test records in the CRM.
        for attempt in range(4):
            status, _, _ = http("DELETE", f"{TWENTY}/rest/{obj}/{rid}",
                                headers={"Authorization": f"Bearer {TWENTY_KEY}"})
            if status < 400:
                gone += 1
                break
            if status != 429:
                break
            time.sleep(5)
    left = len(CREATED) - gone
    print(f"\n  {DIM}cleaned up {gone}/{len(CREATED)} test record(s){OFF}"
          + (f"  {RED}{left} left behind{OFF}" if left else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="Prove the workflows work")
    ap.add_argument("--only", default="",
                    help="lead, consent, link, error, scheduled")
    ap.add_argument("--email", default="admin@aspiretss.com")
    ap.add_argument("--password", default="AspireDemo2026!")
    ap.add_argument("--keep", action="store_true",
                    help="leave the test records in Twenty for inspection")
    args = ap.parse_args()

    if not TWENTY_KEY or not N8N_KEY:
        print("ERROR: TWENTY_API_KEY and N8N_API_KEY must be set in infra/.env",
              file=sys.stderr)
        return 2

    print(f"Twenty  {TWENTY}\nn8n     {N8N}\nMailpit {MAILPIT}")

    try:
        ids = api_workflows()
    except (RuntimeError, error.URLError) as e:
        print(f"ERROR: cannot reach n8n — {e}", file=sys.stderr)
        return 2

    sess = N8nSession()
    if not sess.login(args.email, args.password):
        print("ERROR: n8n login failed — pass --email/--password", file=sys.stderr)
        return 2

    want = {s.strip() for s in args.only.split(",") if s.strip()}

    def run(key, fn, *a):
        if not want or key in want:
            fn(*a)

    run("lead", prove_lead_form)
    run("consent", prove_consent_gate)
    run("link", prove_tracked_link)
    run("inbound", prove_inbound_events)
    run("error", prove_error_handler, sess, ids)
    run("scheduled", prove_scheduled, sess, ids)

    if not args.keep:
        cleanup()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{'=' * 70}")
    print(f"  {passed}/{total} checks passed")
    if passed < total:
        print(f"\n  {RED}Failed:{OFF}")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"    - {name}  {DIM}{detail}{OFF}")
    print(f"{'=' * 70}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
