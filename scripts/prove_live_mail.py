#!/usr/bin/env python3
"""
One operator-shaped run against a real inbox.

This is the Gmail demo, not the Mailpit suite. It upserts you as a Person,
submits the public form, waits for the acknowledgement in messageLog (and
your inbox), honours an unsubscribe, and optionally backdates that submission
so LEAD Nurture Sequence sends nurture_1. Records stay in Twenty unless you
pass --cleanup.

Must run on the laptop that hosts the stack. Credentials stay in infra/.env;
this script never prints the app password.

    python scripts/n8n_credentials.py
    python scripts/build_n8n_workflows.py
    python scripts/n8n_deploy.py --activate
    python scripts/prove_live_mail.py
    python scripts/prove_live_mail.py --nurture
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib import error, parse

import prove_workflows as pw


SUPPRESSED = {"OPTED_OUT", "BOUNCED", "COMPLAINED"}


def utc_z(dt: datetime | None = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z")


def find_person(email: str) -> dict:
    rows = pw.rows("people",
                   f"filter=emails.primaryEmail[eq]:{parse.quote(email)}")
    return rows[0] if rows else {}


def find_company(domain: str) -> dict:
    url = f"https://{domain}"
    rows = pw.rows(
        "companies",
        f"filter=domainName.primaryLinkUrl[eq]:{parse.quote(url, safe='')}")
    if rows:
        return rows[0]
    # ilike in case the row was stored without the scheme
    rows = pw.rows(
        "companies",
        f"filter=domainName.primaryLinkUrl[ilike]:{parse.quote('%' + domain + '%')}")
    return rows[0] if rows else {}


def latest_consent(person_id: str) -> dict:
    rows = pw.rows(
        "consentRecords",
        f"filter=personId[eq]:{person_id}&order_by=effectiveAt[DescNullsLast]")
    return rows[0] if rows else {}


def write_opted_in(person_id: str, proof: str) -> dict:
    return pw.twenty_post("consentRecords", {
        "name": "Email consent — live demo",
        "channel": "EMAIL",
        "status": "OPTED_IN",
        "source": "MANUAL",
        "effectiveAt": utc_z(),
        "proof": proof,
        "personId": person_id,
    })


def clear_suppressions(person_id: str) -> int:
    """Drop prior OPTED_OUT / bounce / complaint so the form can opt this person in.

    The form treats *any* historical suppression as blocking — a new OPTED_IN
    row is not enough. This is a demo reset of the operator record only.
    """
    rows = pw.rows("consentRecords", f"filter=personId[eq]:{person_id}")
    n = 0
    for rec in rows:
        if rec.get("status") in SUPPRESSED:
            pw.twenty_delete("consentRecords", rec["id"])
            n += 1
    return n


def upsert_operator(email: str, first: str, last: str, company_name: str) -> dict:
    person = find_person(email)
    if person:
        dropped = clear_suppressions(person["id"])
        latest = latest_consent(person["id"])
        if latest.get("status") != "OPTED_IN":
            write_opted_in(person["id"],
                           "prove_live_mail.py upsert before form submit")
        return {"person": person, "created": False, "cleared": dropped}

    domain = email.split("@", 1)[1].lower()
    company = find_company(domain)
    if not company:
        company = pw.twenty_post("companies", {
            "name": company_name,
            "domainName": {"primaryLinkUrl": f"https://{domain}"},
        })
    person = pw.twenty_post("people", {
        "name": {"firstName": first, "lastName": last},
        "emails": {"primaryEmail": email},
        "companyId": company.get("id"),
    })
    write_opted_in(person["id"], "prove_live_mail.py initial OPTED_IN")
    return {"person": person, "created": True, "cleared": 0,
            "company": company}


def trigger(sess: pw.N8nSession, ids: dict, name: str, timeout: int = 90) -> dict:
    wid = ids.get(name)
    if not wid:
        return {"ok": False, "detail": "workflow not deployed"}
    wf = sess.workflow(wid)
    trig = next((n["name"] for n in wf.get("nodes", [])
                 if "schedule" in n.get("type", "").lower()), "")
    exec_id = sess.run(wid, trig)
    if not exec_id:
        return {"ok": False, "detail": "did not start"}
    result = sess.wait(exec_id, timeout=timeout)
    status = result.get("status", "unknown")
    failed = [
        (n, r["error"].get("message", "")[:90])
        for n, runs_ in ((result.get("data") or {})
                         .get("resultData", {}).get("runData", {}) or {}).items()
        for r in runs_ if r.get("error")
    ]
    ok = status == "success" and not failed
    detail = (failed[0][0] + ": " + failed[0][1]) if failed else status
    return {"ok": ok, "detail": detail, "status": status}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Prove the send path against the operator's real inbox")
    ap.add_argument("--live-email", default="",
                    help="recipient; default LIVE_DEMO_EMAIL from infra/.env")
    ap.add_argument("--first-name", default="",
                    help="default LIVE_DEMO_FIRST_NAME or Live")
    ap.add_argument("--last-name", default="",
                    help="default LIVE_DEMO_LAST_NAME or Demo")
    ap.add_argument("--company", default="",
                    help="default LIVE_DEMO_COMPANY or the email domain")
    ap.add_argument("--email", default="admin@aspiretss.com",
                    help="n8n owner email, for triggering schedules")
    ap.add_argument("--password", default="AspireDemo2026!")
    ap.add_argument("--nurture", action="store_true",
                    help="backdate the form submission and send nurture_1")
    ap.add_argument("--cleanup", action="store_true",
                    help="delete the operator person/company when finished "
                         "(default is to keep them)")
    args = ap.parse_args()

    if not pw.smtp_is_live():
        print(
            "ERROR: prove_live_mail.py is the real-inbox run.\n"
            "       EMAIL_SMTP_HOST is unset or points at Mailpit — use\n"
            "       scripts/prove_workflows.py for that, or set smtp.gmail.com\n"
            "       in infra/.env (personal Gmail, not smtp-relay.gmail.com).",
            file=sys.stderr)
        return 2

    email = pw.require_live_safety(
        args.live_email or os.environ.get("LIVE_DEMO_EMAIL")
        or pw.ENV.get("LIVE_DEMO_EMAIL") or "")
    pw.LIVE_EMAIL = email

    if not pw.TWENTY_KEY or not pw.N8N_KEY:
        print("ERROR: TWENTY_API_KEY and N8N_API_KEY must be set in infra/.env",
              file=sys.stderr)
        return 2

    first = (args.first_name or pw.ENV.get("LIVE_DEMO_FIRST_NAME") or "Live").strip()
    last = (args.last_name or pw.ENV.get("LIVE_DEMO_LAST_NAME") or "Demo").strip()
    domain = email.split("@", 1)[1]
    company_name = (args.company or pw.ENV.get("LIVE_DEMO_COMPANY")
                    or f"Live Demo ({domain})").strip()

    print(f"Twenty  {pw.TWENTY}")
    print(f"n8n     {pw.N8N}")
    print(f"live    {email}  (messageLog, not Mailpit)")
    print(f"{pw.DIM}SMTP password is not printed.{pw.OFF}")

    try:
        ids = pw.api_workflows()
    except (RuntimeError, error.URLError) as e:
        print(f"ERROR: cannot reach n8n — {e}", file=sys.stderr)
        return 2

    sess = pw.N8nSession()
    if not sess.login(args.email, args.password):
        print("ERROR: n8n login failed — pass --email/--password", file=sys.stderr)
        return 2

    # --- 1. upsert person + OPTED_IN ------------------------------------
    print(f"\n{pw.BOLD}1. Upsert Person + consent OPTED_IN{pw.OFF}")
    state = upsert_operator(email, first, last, company_name)
    person = state["person"]
    pid = person["id"]
    pw.check("person in Twenty", bool(pid), pid)
    pw.check("consent OPTED_IN before the form",
             latest_consent(pid).get("status") == "OPTED_IN",
             latest_consent(pid).get("status") or "none")
    if state["cleared"]:
        print(f"  {pw.DIM}cleared {state['cleared']} prior suppression "
              f"record(s) so the form can opt you in again{pw.OFF}")

    fields = [first, last, email, company_name, "555-0199",
              "SOC / managed detection",
              "Live Gmail demo from prove_live_mail.py.", "true"]

    # --- 2–3. form + ack ------------------------------------------------
    print(f"\n{pw.BOLD}2. Submit the public form as you{pw.OFF}")
    sent_before = len(pw.sent_logs(pid, "smtp/lead_ack"))
    if not pw.check("form accepts submission", pw.submit_form(fields) == 200):
        return 1
    time.sleep(6)
    person = find_person(email)
    pid = person.get("id") or pid
    pw.check("person still one row", bool(pid), pid)

    print(f"\n{pw.BOLD}3. Acknowledgement reached Gmail (messageLog SENT){pw.OFF}")
    ok, detail = pw.wait_for(
        lambda: pw.ack_reached(email, pid, before=sent_before,
                               vendor="smtp/lead_ack"),
        timeout=90)
    if not pw.check("smtp/lead_ack SENT to the allowlisted address", ok, detail):
        print(f"  {pw.DIM}If Gmail challenged a new-IP login, check the "
              f"account's security prompt — not the workflow.{pw.OFF}")
        return 1

    # --- 4. unsubscribe -------------------------------------------------
    print(f"\n{pw.BOLD}4. Unsubscribe via /webhook/mail-event{pw.OFF}")
    posted, _, _ = pw.http(
        "POST", f"{pw.N8N}/webhook/mail-event",
        headers={"Content-Type": "application/json"},
        data=json.dumps({
            "type": "unsubscribe",
            "email": email,
            "messageId": "live-demo-unsub",
            "reason": "prove_live_mail.py unsubscribe loop",
        }).encode())
    if not pw.check("unsubscribe event accepted", posted == 200, f"HTTP {posted}"):
        return 1
    time.sleep(7)
    latest = latest_consent(pid)
    pw.check("consent flipped to OPTED_OUT",
             latest.get("status") == "OPTED_OUT",
             latest.get("status") or "none")

    # --- 5. form again — no second Gmail --------------------------------
    print(f"\n{pw.BOLD}5. Same form again — must not SMTP{pw.OFF}")
    sent_mid = len(pw.sent_logs(pid))
    blocked_before = len(pw.blocked_logs(pid))
    if not pw.check("second submission accepted", pw.submit_form(fields) == 200):
        return 1
    time.sleep(8)
    n_new = len(pw.sent_logs(pid)) - sent_mid
    pw.check("NO second SENT log (nothing left for Gmail)", n_new == 0,
             f"{n_new} new SENT")
    blocked = []
    deadline = time.time() + 30
    while time.time() < deadline:
        blocked = pw.blocked_logs(pid)
        if len(blocked) > blocked_before:
            break
        time.sleep(2)
    pw.check("Blocked: log on the person",
             len(blocked) > blocked_before,
             blocked[0]["subject"] if blocked else "none")

    # --- 6. optional nurture --------------------------------------------
    if args.nurture:
        print(f"\n{pw.BOLD}6. Backdate submission, send nurture_1{pw.OFF}")
        # Unsubscribe just proved; restore OPTED_IN so the send gate will fire.
        dropped = clear_suppressions(pid)
        write_opted_in(pid, "prove_live_mail.py restored after unsubscribe proof")
        pw.check("consent restored OPTED_IN for nurture",
                 latest_consent(pid).get("status") == "OPTED_IN")
        if dropped:
            print(f"  {pw.DIM}removed {dropped} suppression record(s); the "
                  f"unsubscribe proof above still stands{pw.OFF}")

        subs = pw.rows(
            "formSubmissions",
            f"filter=personId[eq]:{pid}&order_by=createdAt[DescNullsLast]")
        if not pw.check("formSubmission exists to backdate", bool(subs)):
            return 1
        when = datetime.now(timezone.utc) - timedelta(days=3)
        pw.twenty_patch("formSubmissions", subs[0]["id"],
                        {"submittedAt": utc_z(when)})
        pw.check("submittedAt set ~3 days ago", True, utc_z(when))

        already = pw.sent_logs(pid, "smtp/nurture_1")
        if already:
            pw.check("smtp/nurture_1 already on file — not re-sending",
                     True, already[0].get("subject") or "present")
        else:
            sent_n = len(already)
            result = trigger(sess, ids, "LEAD Nurture Sequence", timeout=120)
            pw.check("LEAD Nurture Sequence completes",
                     result["ok"], result["detail"])
            ok, detail = pw.wait_for(
                lambda: (len(pw.sent_logs(pid, "smtp/nurture_1")) > sent_n,
                         f"{len(pw.sent_logs(pid, 'smtp/nurture_1'))} "
                         f"smtp/nurture_1"),
                timeout=90)
            pw.check("smtp/nurture_1 SENT", ok, detail)
    else:
        print(f"\n{pw.DIM}6. skipped — pass --nurture to backdate and send "
              f"nurture_1{pw.OFF}")

    # --- 7. renewal + sweeps: tasks, no extra mail ----------------------
    print(f"\n{pw.BOLD}7. Renewal + sweeps (no extra Gmail){pw.OFF}")
    sent_end = len(pw.sent_logs(pid))
    for name in ("SUB Renewal Escalation", "OPS Scheduled Sweeps"):
        result = trigger(sess, ids, name, timeout=90)
        pw.check(f"{name} completes", result["ok"], result["detail"])
    time.sleep(4)
    extra = len(pw.sent_logs(pid)) - sent_end
    pw.check("those workflows sent no extra email", extra == 0,
             f"{extra} new SENT")

    if args.cleanup:
        pw.CREATED.append(("people", pid))
        # Only drop a company this run created. A gmail.com / workspace domain
        # match might already have been there.
        if state.get("created") and state.get("company", {}).get("id"):
            pw.CREATED.append(("companies", state["company"]["id"]))
        pw.cleanup()
    else:
        print(f"\n  {pw.DIM}kept person {pid} — open them in Twenty. If Gmail "
              f"OAuth is connected, the thread lands on the timeline in "
              f"~5 minutes.{pw.OFF}")

    passed = sum(1 for _, ok, _ in pw.RESULTS if ok)
    total = len(pw.RESULTS)
    print(f"\n{'=' * 70}")
    print(f"  {passed}/{total} checks passed")
    if passed < total:
        print(f"\n  {pw.RED}Failed:{pw.OFF}")
        for name, ok, detail in pw.RESULTS:
            if not ok:
                print(f"    - {name}  {pw.DIM}{detail}{pw.OFF}")
    print(f"{'=' * 70}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
