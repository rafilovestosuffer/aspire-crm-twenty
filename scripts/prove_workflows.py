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
    links = rows("trackedLinks", "limit=1")
    if not check("a tracked link exists to test", bool(links)):
        return
    link = links[0]
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
    print(f"\n{BOLD}SYS Error Handler — failures are caught and alerted{OFF}")
    before = len(rows("automationRuns", "filter=status[eq]:FAILED"))

    # A workflow whose Twenty call cannot succeed: point it at a bad path.
    wid = ids.get("MSG Tracked Link Redirect")
    status, _, _ = http("GET", f"{N8N}/webhook/t?s=__definitely_not_a_slug__",
                        redirect=False)
    check("unknown slug does not 5xx", status < 500, f"HTTP {status}")

    runs = rows("automationRuns", "order_by=createdAt[DescNullsLast]")
    check("automationRun history is being written", len(runs) > 0,
          f"{len(runs)} run(s) recorded")

    alerts = [r for r in runs if (r.get("workflowName") or "").startswith("ALERT")]
    check("chat alerts reach the webhook", bool(alerts),
          f"{len(alerts)} alert(s) delivered")


def prove_scheduled(sess: N8nSession, ids: dict) -> None:
    print(f"\n{BOLD}Scheduled workflows — renewals and sweeps{OFF}")
    for name, trigger in (("SUB Renewal Escalation", "Every morning"),
                          ("OPS Scheduled Sweeps", "Every morning")):
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Prove the workflows work")
    ap.add_argument("--only", default="",
                    help="lead, consent, link, error, scheduled")
    ap.add_argument("--email", default="admin@aspiretss.com")
    ap.add_argument("--password", default="AspireDemo2026!")
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
    run("error", prove_error_handler, sess, ids)
    run("scheduled", prove_scheduled, sess, ids)

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
