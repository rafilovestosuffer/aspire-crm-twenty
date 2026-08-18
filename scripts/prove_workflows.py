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

Requires the dev profile by default: Mailpit catches the mail, the alert sink
catches the chat webhook. Nothing leaves the host.

    ./infra/up.sh
    python scripts/n8n_credentials.py
    python scripts/n8n_deploy.py --dev
    python scripts/prove_workflows.py

Against a real relay (Gmail, Workspace), Mailpit is not in the path. The suite
refuses to run unless --live-email (or LIVE_DEMO_EMAIL) is set *and* sits on
LIVE_MAIL_ALLOWLIST — otherwise a rebuild would SMTP the fake proof domains.

    python scripts/prove_workflows.py --live-email you@gmail.com

On a VPS, Mailpit and --dev are absent. --production skips the failure probe,
requires a real relay and --live-email, and allows an empty allowlist (that is
the production setting). Run it only against an empty CRM: triggering nurture
on real leads would SMTP them.

    python scripts/prove_workflows.py --production --live-email you@...

Usage:
    python scripts/prove_workflows.py
    python scripts/prove_workflows.py --only consent
    python scripts/prove_workflows.py --live-email you@gmail.com
    python scripts/prove_workflows.py --production --live-email you@gmail.com
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
    for k in ("TWENTY_BASE_URL", "TWENTY_API_KEY", "N8N_BASE_URL", "N8N_API_KEY",
              "LIVE_DEMO_EMAIL", "LIVE_MAIL_ALLOWLIST", "EMAIL_SMTP_HOST"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


ENV = read_env()
TWENTY = ENV.get("TWENTY_BASE_URL", "http://localhost:3000")
TWENTY_KEY = ENV.get("TWENTY_API_KEY", "")
N8N = ENV.get("N8N_BASE_URL", "http://localhost:5678")
N8N_KEY = ENV.get("N8N_API_KEY", "")
MAILPIT = ENV.get("MAILPIT_URL", "http://localhost:8025")

# Catcher hosts: n8n_credentials.py defaults EMAIL_SMTP_HOST to mailpit when
# unset. Anything else is a real relay and must not receive proof domains.
CATCHER_HOSTS = frozenset({"", "mailpit", "localhost", "127.0.0.1"})
LIVE_EMAIL = ""


def smtp_is_live(env: dict | None = None) -> bool:
    host = ((env or ENV).get("EMAIL_SMTP_HOST") or "").strip().lower()
    return host not in CATCHER_HOSTS


def parse_allowlist(env: dict | None = None) -> list[str]:
    raw = (env or ENV).get("LIVE_MAIL_ALLOWLIST") or ""
    return [a.strip().lower() for a in raw.split(",") if a.strip()]


def require_live_safety(live_email: str, env: dict | None = None,
                       *, allow_empty_allowlist: bool = False) -> str:
    """Refuse to prove against a real relay unless the recipient is allowlisted.

    An empty allowlist means VEND Send Email will SMTP anyone who consented —
    including the fake proof.{tag}@northgate-{tag}.com addresses this suite
    invents. That is the defect a live Gmail .env would hit on rebuild.sh.

    --production on a VPS is the exception: LIVE_MAIL_ALLOWLIST must stay
    empty there (preflight blocks otherwise), and this suite only sends to
    LIVE_EMAIL. Consent-refuse tests keep a non-consenting fake address.
    """
    env = env or ENV
    email = (live_email or env.get("LIVE_DEMO_EMAIL") or "").strip()
    if not email or "@" not in email:
        print(
            "ERROR: EMAIL_SMTP_HOST is a real relay "
            f"({env.get('EMAIL_SMTP_HOST')}) but no live recipient was given.\n"
            "       This suite submits the form as proof.<tag>@northgate-<tag>.com\n"
            "       — those are not real, and Gmail would still try to deliver.\n"
            "       Pass --live-email you@gmail.com (and set LIVE_MAIL_ALLOWLIST\n"
            "       to the same address), or clear EMAIL_SMTP_* to use Mailpit.",
            file=sys.stderr)
        sys.exit(2)
    allow = parse_allowlist(env)
    if not allow:
        if allow_empty_allowlist:
            print(
                "WARNING: LIVE_MAIL_ALLOWLIST is empty — consent is the only "
                "send-time gate.\n         Opted-in proofs use "
                f"{email} only. Do not run this against a CRM that already "
                "has real leads due for nurture.",
                file=sys.stderr)
            return email
        print(
            "ERROR: EMAIL_SMTP_HOST is a real relay but LIVE_MAIL_ALLOWLIST is "
            "empty.\n       Without it, seed .example.com addresses and proof "
            "domains are eligible to send.\n       Set LIVE_MAIL_ALLOWLIST="
            f"{email} in infra/.env and recreate n8n so the env var reaches "
            "the container.",
            file=sys.stderr)
        sys.exit(2)
    if email.lower() not in allow:
        print(
            f"ERROR: {email} is not on LIVE_MAIL_ALLOWLIST ({', '.join(allow)}).\n"
            "       The send workflow would refuse the ack, and the suite "
            "would fail for the wrong reason.",
            file=sys.stderr)
        sys.exit(2)
    return email


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


def twenty_patch(obj: str, rid: str, body: dict) -> dict:
    status, raw, _ = http("PATCH", f"{TWENTY}/rest/{obj}/{rid}",
                          headers={"Authorization": f"Bearer {TWENTY_KEY}",
                                   "Content-Type": "application/json"},
                          data=json.dumps(body).encode())
    if status >= 400:
        raise RuntimeError(f"Twenty {status}: {raw[:200].decode('utf-8','replace')}")
    d = json.loads(raw or "{}").get("data", {})
    return next(iter(d.values()), {}) if d else {}


def twenty_delete(obj: str, rid: str) -> int:
    status, raw, _ = http("DELETE", f"{TWENTY}/rest/{obj}/{rid}",
                          headers={"Authorization": f"Bearer {TWENTY_KEY}"})
    if status >= 400 and status != 404:
        raise RuntimeError(f"Twenty {status}: {raw[:200].decode('utf-8','replace')}")
    return status


def rows(obj: str, query: str = "") -> list[dict]:
    """One page of records. 200 is Twenty's hard ceiling, not a choice.

    Asking for more returns 200 anyway, with HTTP 200 and no warning, so a
    check written as "fetch everything and count what matches" quietly stops
    being true the day the table passes 200 rows — which is how the alert check
    below started failing on a stack where alerting worked perfectly. Anything
    counting occurrences must filter server-side and read `count()`.
    """
    parts = [q for q in query.split("&") if q]
    if not any(q.startswith("limit=") for q in parts):
        parts.append("limit=200")
    d = twenty(f"/rest/{obj}?" + "&".join(parts))
    return d.get("data", {}).get(obj, [])


def count(obj: str, query: str = "") -> int:
    """How many records match, server-side — independent of any page size."""
    parts = [q for q in query.split("&") if q] + ["limit=1"]
    return twenty(f"/rest/{obj}?" + "&".join(parts)).get("totalCount", 0)


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
    try:
        http("DELETE", f"{MAILPIT}/api/v1/messages")
    except error.URLError:
        pass


def mailpit_messages() -> list[dict]:
    try:
        status, body, _ = http("GET", f"{MAILPIT}/api/v1/messages?limit=50")
    except error.URLError:
        return []
    if status >= 400:
        return []
    return json.loads(body or "{}").get("messages", [])


def sent_logs(person_id: str, vendor: str = "") -> list[dict]:
    logs = rows("messageLogs",
                f"filter=personId[eq]:{person_id}"
                "&order_by=createdAt[DescNullsLast]")
    out = [m for m in logs if (m.get("status") or "") == "SENT"]
    if vendor:
        out = [m for m in out if (m.get("vendor") or "") == vendor]
    return out


def blocked_logs(person_id: str) -> list[dict]:
    logs = rows("messageLogs",
                f"filter=personId[eq]:{person_id}"
                "&order_by=createdAt[DescNullsLast]")
    return [m for m in logs if (m.get("subject") or "").startswith("Blocked:")]


def wait_for(pred, timeout: float = 0, interval: float = 2.0):
    """Call pred() until it returns a truthy first element, or timeout.

    pred must return (ok, detail). timeout=0 is a single check — Mailpit is
    already in the path after the sleep the caller did.
    """
    last = pred()
    deadline = time.time() + timeout
    while not last[0] and time.time() < deadline:
        time.sleep(interval)
        last = pred()
    return last


def ack_reached(email: str, person_id: str, *, before: int = 0,
                vendor: str = "smtp/lead_ack") -> tuple[bool, str]:
    """Did the acknowledgement actually leave? Mailpit locally, messageLog live."""
    if LIVE_EMAIL:
        sent = sent_logs(person_id, vendor)
        n = len(sent)
        detail = (sent[0].get("vendor") or sent[0].get("subject")
                  if sent else "no SENT log")
        return n > before, f"{detail} ({n} SENT, was {before})"
    mail = mailpit_messages()
    to = mail[0]["To"][0]["Address"] if mail else ""
    return to == email, to or "no mail"


def no_mail_left(person_id: str) -> tuple[bool, str]:
    if LIVE_EMAIL:
        sent = sent_logs(person_id)
        return not sent, f"{len(sent)} SENT log(s)"
    n = len(mailpit_messages())
    return n == 0, f"{n} message(s)"


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


def _iso_in(**delta) -> str:
    """A UTC timestamp Twenty will accept. Offsets like +00:00 are a 400 in
    both filters and write bodies, so this always renders a trailing Z."""
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(**delta)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


def prove_lead_form() -> None:
    print(f"\n{BOLD}LEAD Form Intake — public form to CRM record{OFF}")
    tag = uuid.uuid4().hex[:8]
    email = LIVE_EMAIL or f"proof.{tag}@northgate-{tag}.com"
    existing = rows("people", f"filter=emails.primaryEmail[eq]:{parse.quote(email)}")
    sent_before = (len(sent_logs(existing[0]["id"], "smtp/lead_ack"))
                   if existing else 0)
    if not LIVE_EMAIL:
        mailpit_clear()

    # Positional, so the order must match the form trigger exactly:
    # first, last, email, company, phone, interest, certification, message,
    # consent. Adding a field to the form without changing this sends every
    # later value into the wrong slot, and the form still answers 200.
    status = submit_form(["Proof", f"Lead{tag}", email, f"Northgate {tag}",
                          "555-0100", "Splunk bootcamp",
                          "Splunk Core Certified User",
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

    ok, detail = wait_for(
        lambda: ack_reached(email, person["id"], before=sent_before),
        timeout=60 if LIVE_EMAIL else 0)
    check("acknowledgement sent to the right address", ok, detail)


def prove_consent_gate() -> None:
    print(f"\n{BOLD}Consent gate — the send must be refused{OFF}")
    tag = uuid.uuid4().hex[:8]
    # Always a fake, non-allowlisted address. Live SMTP must not see this.
    email = f"noconsent.{tag}@bluffpoint-{tag}.com"
    if not LIVE_EMAIL:
        mailpit_clear()

    status = submit_form(["NoConsent", f"Test{tag}", email, f"Bluffpoint {tag}",
                          "555-0101", "An upcoming webinar or workshop",
                          "Not sure yet",
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

    ok, detail = no_mail_left(people[0]["id"])
    check("NO email was sent", ok, detail)

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

    # Counted server-side, both of them. Reading a page and filtering it here
    # worked until automationRuns passed 200 rows, at which point the alerts
    # fell outside the page and this check reported "0 new alert(s)" against a
    # stack where alerting was working — a false alarm indistinguishable from
    # the real failure it exists to catch.
    ALERTS = "filter=workflowName[ilike]:ALERT%25"
    before = count("automationRuns", "filter=status[eq]:FAILED")
    alerts_before = count("automationRuns", ALERTS)

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
    failed, now_failed, now_alerts = [], before, alerts_before
    for _ in range(15):
        time.sleep(2)
        now_failed = count("automationRuns", "filter=status[eq]:FAILED")
        now_alerts = count("automationRuns", ALERTS)
        if now_failed > before and now_alerts > alerts_before:
            break
    failed = rows("automationRuns", "filter=status[eq]:FAILED"
                                    "&order_by=createdAt[DescNullsLast]&limit=1")

    check("failure recorded in Twenty as automationRun FAILED",
          now_failed > before,
          failed[0].get("workflowName", "") if failed else "none")
    check("failure message captured, not swallowed",
          bool(failed) and "deliberate failure probe" in
          (failed[0].get("errorMessage") or ""),
          (failed[0].get("errorMessage") or "")[:60] if failed else "")
    check("alert delivered for the failure", now_alerts > alerts_before,
          f"{now_alerts - alerts_before} new alert(s)")


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
    email = LIVE_EMAIL or f"loop.{tag}@ridgeline-{tag}.com"
    fields = ["Loop", f"Test{tag}", email, f"Ridgeline {tag}", "555-0144",
              "Cloud security certification training", "Not sure yet",
              "Consent loop proof.", "true"]

    # --- 1. opted in: the acknowledgement must actually send -------------
    existing = rows("people", f"filter=emails.primaryEmail[eq]:{parse.quote(email)}")
    sent_before = (len(sent_logs(existing[0]["id"], "smtp/lead_ack"))
                   if existing else 0)
    if not LIVE_EMAIL:
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

    ok, detail = wait_for(
        lambda: ack_reached(email, pid, before=sent_before),
        timeout=60 if LIVE_EMAIL else 0)
    if not check("acknowledgement sent while opted in", ok, detail):
        return
    sent_after_ack = len(sent_logs(pid))

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
    # Live SMTP: count SENT logs, not Mailpit. Mailpit is not in the path,
    # and the person already has the first ack on file. Sleep first — a
    # "no new mail" check that returns immediately would miss a late send.
    if not LIVE_EMAIL:
        mailpit_clear()
    if not check("second submission accepted", submit_form(fields) == 200):
        return
    time.sleep(8 if LIVE_EMAIL else 7)

    if LIVE_EMAIL:
        n_new = len(sent_logs(pid)) - sent_after_ack
        check("NO second email sent after opting out", n_new == 0,
              f"{n_new} new SENT log(s)")
    else:
        check("NO second email sent after opting out",
              len(mailpit_messages()) == 0,
              f"{len(mailpit_messages())} message(s)")

    blocked = []
    deadline = time.time() + (30 if LIVE_EMAIL else 0)
    while True:
        blocked = blocked_logs(pid)
        if blocked or time.time() >= deadline:
            break
        time.sleep(2)
    check("refusal logged against the person", bool(blocked),
          blocked[0]["subject"] if blocked else "none")


def post_json(path: str, body: dict) -> tuple[int, dict]:
    """POST JSON to an n8n webhook. A 200 says the webhook received it and
    nothing at all about whether the workflow succeeded — every caller below
    asserts on the record that was written, never on this status."""
    status, raw, _ = http("POST", f"{N8N}/webhook/{path}",
                          headers={"Content-Type": "application/json"},
                          data=json.dumps(body).encode())
    try:
        return status, json.loads(raw or "{}")
    except json.JSONDecodeError:
        return status, {}


def prove_webinar() -> None:
    """Registration through to a confirmation, on the busiest path in the
    business. Asserts the registration record, not the webhook's 200."""
    print(f"\n{BOLD}EVT Webinar Registration — the top of the funnel{OFF}")
    tag = uuid.uuid4().hex[:8]
    email = LIVE_EMAIL or f"webinar.{tag}@crestwood-{tag}.com"

    existing = rows("people", f"filter=emails.primaryEmail[eq]:{parse.quote(email)}")
    sent_before = (len(sent_logs(existing[0]["id"], "smtp/webinar_confirm"))
                   if existing else 0)
    if not LIVE_EMAIL:
        mailpit_clear()

    try:
        event = twenty_post("webinarEvents", {
            "name": f"Proof webinar {tag}",
            "topic": "Splunk SIEM migration",
            "status": "OPEN_FOR_REGISTRATION",
            "scheduledAt": _iso_in(hours=30),
            "registrationCount": 0,
            "attendeeCount": 0,
        })
    except RuntimeError as e:
        check("a webinar exists to register for", False, str(e)[:90])
        return
    CREATED.append(("webinarEvents", event["id"]))
    check("a webinar exists to register for", bool(event.get("id")), tag)

    status, _ = post_json("webinar-register", {
        "email": email, "firstName": "Proof", "lastName": f"Webinar{tag}",
        "webinarId": event["id"], "webinarTitle": event["name"],
        "source": "Email", "consent": True,
    })
    if not check("registration webhook accepts the post", status < 400,
                 f"HTTP {status}"):
        return
    time.sleep(7)

    people = rows("people", f"filter=emails.primaryEmail[eq]:{parse.quote(email)}")
    if not check("person created", len(people) == 1, f"{len(people)} found"):
        return
    pid = people[0]["id"]
    CREATED.append(("people", pid))

    regs = rows("webinarRegistrations", f"filter=personId[eq]:{pid}")
    if check("registration linked to person and event", bool(regs),
             f"{len(regs)} found"):
        CREATED.append(("webinarRegistrations", regs[0]["id"]))
        check("registration points at the right webinar",
              regs[0].get("webinarEventId") == event["id"],
              str(regs[0].get("webinarEventId")))
        check("reminder counter starts at zero",
              (regs[0].get("remindersSent") or 0) == 0,
              str(regs[0].get("remindersSent")))

    consent = rows("consentRecords", f"filter=personId[eq]:{pid}")
    check("consent recorded for the registration",
          bool(consent) and consent[0].get("status") == "OPTED_IN",
          consent[0].get("status") if consent else "none")

    ok, detail = wait_for(
        lambda: ack_reached(email, pid, before=sent_before,
                            vendor="smtp/webinar_confirm"),
        timeout=60 if LIVE_EMAIL else 0)
    check("confirmation sent", ok, detail)


def prove_enrolment() -> None:
    """The revenue path. An enrolment must never be recorded as paid just
    because a payment link was sent."""
    print(f"\n{BOLD}ENR Bootcamp Enrolment — the revenue path{OFF}")
    tag = uuid.uuid4().hex[:8]
    email = LIVE_EMAIL or f"enrol.{tag}@fairhaven-{tag}.com"
    if not LIVE_EMAIL:
        mailpit_clear()

    status, _ = post_json("enrol", {
        "email": email, "firstName": "Proof", "lastName": f"Enrol{tag}",
        "tier": "GOLD", "programName": f"Proof bootcamp {tag}", "amount": 1500,
    })
    if not check("enrolment webhook accepts the post", status < 400,
                 f"HTTP {status}"):
        return
    time.sleep(7)

    people = rows("people", f"filter=emails.primaryEmail[eq]:{parse.quote(email)}")
    if not check("person created", len(people) == 1, f"{len(people)} found"):
        return
    pid = people[0]["id"]
    CREATED.append(("people", pid))

    enrols = rows("enrollments", f"filter=personId[eq]:{pid}")
    if not check("enrolment created", bool(enrols), f"{len(enrols)} found"):
        return
    CREATED.append(("enrollments", enrols[0]["id"]))
    e = enrols[0]
    check("tier recorded as sent", e.get("tier") == "GOLD", str(e.get("tier")))
    # The single most important assertion here. Marking an enrolment PAID on
    # the strength of a link being emailed would put unpaid students in the
    # room and take them out of the chase.
    check("status is PAYMENT_SENT, not PAID",
          e.get("status") == "PAYMENT_SENT", str(e.get("status")))
    check("a payment link was attached",
          bool((e.get("paymentLinkUrl") or {}).get("primaryLinkUrl")),
          str((e.get("paymentLinkUrl") or {}).get("primaryLinkUrl"))[:50])

    tasks = rows("tasks", "order_by=createdAt[DescNullsLast]")
    hit = [t for t in tasks if tag in (t.get("title") or "")
           or "GOLD" in (t.get("title") or "")]
    check("a task was raised for a human", bool(hit),
          hit[0]["title"] if hit else "none")


def prove_enrolment_rejects_bad_tier() -> None:
    """An unknown tier must fail loudly. Falling back to a default would
    charge the wrong amount, which is a refund and an apology."""
    print(f"\n{BOLD}ENR Bootcamp Enrolment — an unknown tier is refused{OFF}")
    tag = uuid.uuid4().hex[:8]
    email = f"badtier.{tag}@fairhaven-{tag}.com"
    before = count("enrollments")

    post_json("enrol", {"email": email, "firstName": "Bad",
                        "lastName": f"Tier{tag}", "tier": "UNOBTAINIUM"})
    time.sleep(5)

    people = rows("people", f"filter=emails.primaryEmail[eq]:{parse.quote(email)}")
    for p in people:
        CREATED.append(("people", p["id"]))
    after = count("enrollments")
    check("no enrolment created for an unknown tier", after == before,
          f"{before} -> {after}")


def prove_frontdesk_ai() -> None:
    print(f"\n{BOLD}AI FrontDesk Handover — conversations keep their history{OFF}")
    tag = uuid.uuid4().hex[:8]
    email = f"fdai.{tag}@brookline-{tag}.com"

    status, _ = post_json("frontdesk-ai", {
        "email": email, "firstName": "Proof", "lastName": f"AI{tag}",
        "fdai_intent": "asking about the Splunk bootcamp price",
        "fdai_convo_summary": f"Proof conversation {tag}.",
        "fdai_handover_reason": "wants to speak to a trainer",
        "fdai_nps": 9,
        "fdai_page_url": "https://aspiretss.com/cyber-security-certifications",
    })
    if not check("AI webhook accepts the post", status < 400, f"HTTP {status}"):
        return
    time.sleep(7)

    people = rows("people", f"filter=emails.primaryEmail[eq]:{parse.quote(email)}")
    if not check("person created", len(people) == 1, f"{len(people)} found"):
        return
    pid = people[0]["id"]
    CREATED.append(("people", pid))

    convos = rows("aiConversations", f"filter=personId[eq]:{pid}")
    if not check("conversation stored as its own record", bool(convos),
                 f"{len(convos)} found"):
        return
    CREATED.append(("aiConversations", convos[0]["id"]))
    c = convos[0]
    check("the summary survived", tag in (c.get("summary") or ""),
          (c.get("summary") or "")[:40])
    check("NPS captured as a number", c.get("nps") == 9, str(c.get("nps")))
    check("marked as handed over", c.get("handedOver") is True,
          str(c.get("handedOver")))

    tasks = rows("tasks", "order_by=createdAt[DescNullsLast]")
    hit = [t for t in tasks if "AI handover" in (t.get("title") or "")]
    check("handover raised a task", bool(hit),
          hit[0]["title"][:50] if hit else "none")


def prove_tag_sync(sess: N8nSession, ids: dict) -> None:
    print(f"\n{BOLD}SEG Tag Sync — 57 tags get a migration decision{OFF}")
    tag = uuid.uuid4().hex[:8]
    # A cohort-shaped name, which the classifier should send to a cohort record
    # rather than keep as a label.
    try:
        row = twenty_post("crmTags", {
            "name": f"registered splunk bootcamp jan-{tag}",
            "ghlTagName": f"registered splunk bootcamp jan-{tag}",
        })
    except RuntimeError as e:
        check("a tag exists to classify", False, str(e)[:90])
        return
    CREATED.append(("crmTags", row["id"]))
    check("a tag exists to classify", bool(row.get("id")), tag)

    name = "SEG Tag Sync"
    wid = ids.get(name)
    if not check(f"{name} deployed", bool(wid), "" if wid else "not found"):
        return
    wf = sess.workflow(wid)
    trig = next((n["name"] for n in wf.get("nodes", [])
                 if "schedule" in n.get("type", "").lower()), "Daily 05:00 UTC")
    exec_id = sess.run(wid, trig)
    if not check(f"{name} starts", bool(exec_id)):
        return
    sess.wait(exec_id)
    time.sleep(4)

    after = rows("crmTags", f"filter=id[eq]:{row['id']}")
    got = after[0] if after else {}
    check("tag classified as a cohort segment",
          got.get("category") == "SEGMENT", str(got.get("category")))
    check("recommended action is to convert it to a field",
          got.get("migrationAction") == "CONVERT_TO_FIELD",
          str(got.get("migrationAction")))


def prove_scheduled(sess: N8nSession, ids: dict) -> None:
    print(f"\n{BOLD}Scheduled workflows — every job that runs unattended{OFF}")
    for name, trigger in (("SUB Renewal Escalation", "Every morning"),
                          ("OPS Scheduled Sweeps", "Every morning"),
                          ("LEAD Nurture Sequence", "Every morning"),
                          ("ENR Cohort Operations", "Every morning"),
                          ("EVT Webinar Reminders", "Hourly"),
                          ("EVT Post-Webinar Follow-up", "Every morning"),
                          ("BOOK Trainer Appointment", "Every morning")):
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


def prove_health_check(sess: N8nSession, ids: dict) -> None:
    """The health check is the thing that notices silence. Prove it runs.

    A first-day VPS has no yesterday's scheduled runs, so the check may
    correctly decide the stack is 'unhealthy' and alert. That is not a
    failed proof — the proof is that it ran, judged, and wrote an
    automationRun. Whether the Chat webhook delivered is a separate check
    (laptop --dev, or a real ALERT_WEBHOOK_URL).
    """
    print(f"\n{BOLD}SYS Daily Health Check — the stack notices silence{OFF}")
    name = "SYS Daily Health Check"
    wid = ids.get(name)
    if not check(f"{name} deployed", bool(wid),
                 "" if wid else "not found"):
        return
    wf = sess.workflow(wid)
    trig = next((n["name"] for n in wf.get("nodes", [])
                 if "schedule" in n.get("type", "").lower()), "Daily 09:00 UTC")
    before = count("automationRuns",
                   f"filter=workflowName[eq]:{parse.quote(name)}")
    exec_id = sess.run(wid, trig)
    if not check(f"{name} starts", bool(exec_id)):
        return
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

    wrote = False
    now_count = before
    for _ in range(15):
        time.sleep(2)
        now_count = count("automationRuns",
                          f"filter=workflowName[eq]:{parse.quote(name)}")
        if now_count > before:
            wrote = True
            break
    check("health check wrote an automationRun", wrote,
          f"{now_count - before} new row(s)")


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
                    help="lead, consent, link, inbound, webinar, enrol, ai, "
                         "tags, error, scheduled, health")
    ap.add_argument("--email", default="admin@aspiretss.com")
    ap.add_argument("--password", default="AspireDemo2026!")
    ap.add_argument("--keep", action="store_true",
                    help="leave the test records in Twenty for inspection")
    ap.add_argument("--live-email", default="",
                    help="send opted-in proofs to this address (required when "
                         "EMAIL_SMTP_HOST is a real relay). Also reads "
                         "LIVE_DEMO_EMAIL from the environment.")
    ap.add_argument("--production", action="store_true",
                    help="VPS prove: no Mailpit, no --dev failure probe, "
                         "requires a real SMTP relay and --live-email. "
                         "Run only against an empty CRM.")
    args = ap.parse_args()

    global LIVE_EMAIL
    live_flag = (args.live_email or os.environ.get("LIVE_DEMO_EMAIL")
                 or ENV.get("LIVE_DEMO_EMAIL") or "").strip()
    if args.production:
        if not smtp_is_live():
            print(
                "ERROR: --production is the VPS prove. EMAIL_SMTP_HOST is "
                "unset or points at Mailpit.\n       Use prove_workflows.py "
                "without --production on a laptop, or set the real relay.",
                file=sys.stderr)
            return 2
        LIVE_EMAIL = require_live_safety(live_flag, allow_empty_allowlist=True)
    elif smtp_is_live():
        LIVE_EMAIL = require_live_safety(live_flag)
    elif live_flag:
        LIVE_EMAIL = live_flag

    if not TWENTY_KEY or not N8N_KEY:
        print("ERROR: TWENTY_API_KEY and N8N_API_KEY must be set in infra/.env",
              file=sys.stderr)
        return 2

    print(f"Twenty  {TWENTY}\nn8n     {N8N}")
    if args.production:
        print("mode    production (messageLog, no failure probe)")
    if LIVE_EMAIL:
        print(f"live    {LIVE_EMAIL}  (asserting messageLog, not Mailpit)")
    elif not args.production:
        print(f"Mailpit {MAILPIT}")

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
    run("webinar", prove_webinar)
    run("enrol", prove_enrolment)
    run("enrol", prove_enrolment_rejects_bad_tier)
    run("ai", prove_frontdesk_ai)
    run("tags", prove_tag_sync, sess, ids)
    if args.production:
        if not want or "error" in want:
            print(f"\n{BOLD}SYS Error Handler — skipped on --production{OFF}")
            print(f"  {DIM}Delivery is proven on the laptop with --dev. On the "
                  f"VPS, POST ALERT_WEBHOOK_URL or wait for a real failure.{OFF}")
    else:
        run("error", prove_error_handler, sess, ids)
    run("scheduled", prove_scheduled, sess, ids)
    run("health", prove_health_check, sess, ids)

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
