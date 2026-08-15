#!/usr/bin/env python3
"""
Honest implementation status for every catalogued GoHighLevel feature.

The replacement guide says how each feature *would* be replaced. This says
what actually exists right now, on the running stack. The two are very
different things and conflating them is how a migration gets declared finished
three weeks before it is.

Status levels, strongest to weakest:

    LIVE       verified working against the running stack
    BUILT      code committed, never executed — the dangerous one
    MODELLED   a Twenty object exists to hold the data, no automation yet
    NATIVE     Twenty does it out of the box, needs configuration only
    DESIGNED   specified in the guide, nothing built
    DEFERRED   deliberately out of scope, with a reason
    NOT-STARTED

Usage:
    python scripts/implementation_audit.py
    python scripts/implementation_audit.py --markdown > out/implementation_status.md
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parent.parent
TAXONOMY = ROOT / "reference" / "ghl_feature_taxonomy.csv"
WORKFLOW_DIR = ROOT / "n8n" / "workflows"

LIVE, BUILT, MODELLED = "LIVE", "BUILT", "MODELLED"
NATIVE, DESIGNED, DEFERRED, NOT_STARTED = "NATIVE", "DESIGNED", "DEFERRED", "NOT-STARTED"

ORDER = [LIVE, BUILT, NATIVE, MODELLED, DESIGNED, DEFERRED, NOT_STARTED]
COLOUR = {LIVE: "\033[32m", BUILT: "\033[33m", NATIVE: "\033[36m",
          MODELLED: "\033[36m", DESIGNED: "\033[35m", DEFERRED: "\033[2m",
          NOT_STARTED: "\033[31m"}
RESET = "\033[0m"

# Feature id -> (status, evidence). Anything not listed falls through to the
# rules below. Only put a feature here when there is a specific reason.
#
# LIVE is deliberately NOT allowed here, and `_assert_no_asserted_live` below
# enforces it. LIVE means "this was observed working on the running stack a
# moment ago" — the one claim in this file that must never be a remembered
# result. An earlier version hard-coded EM-05 as LIVE from a verified run, and
# because EXPLICIT is applied last it would have kept reporting LIVE long after
# the workflow stopped working. Status that outlives its evidence is exactly
# what this script exists to prevent.
EXPLICIT: dict[str, tuple[str, str]] = {
    # Twenty does these natively; they need configuration, not building.
    "CT-01": (NATIVE, "Twenty saved views"),
    "CT-02": (NATIVE, "Twenty bulk edit"),
    "CT-03": (NATIVE, "Twenty CSV import"),
    "CT-06": (NATIVE, "Twenty notes and tasks"),
    "OP-01": (NATIVE, "Twenty opportunity stages"),
    "OP-03": (NATIVE, "Twenty record-updated trigger"),
    "US-01": (NATIVE, "Twenty workspace members"),
    "US-02": (NATIVE, "Twenty roles"),
    "US-03": (NATIVE, "Twenty teams"),
    "ST-01": (NATIVE, "Twenty workspace settings"),
    "CV-01": (NATIVE, "Twenty mailbox sync — email only, needs connecting"),

    # Deliberately out of scope, with the reason.
    "CV-02": (DEFERRED, "SMS — carrier required, barely used"),
    "CV-03": (DEFERRED, "WhatsApp — Meta approved provider required"),
    "CV-04": (DEFERRED, "social DM — barely used"),
    "CV-05": (DEFERRED, "GMB — barely used"),
    "CV-06": (DEFERRED, "live chat — no vendor chosen"),
    "SM-01": (DEFERRED, "SMS — carrier required"),
    "SM-02": (DEFERRED, "numbers — carrier required"),
    "SM-03": (DEFERRED, "A2P — carrier required"),
    "SM-04": (DEFERRED, "IVR — carrier required"),
    "SM-05": (DEFERRED, "call recording — carrier required"),
    "SM-06": (DEFERRED, "voicemail drop — carrier required"),
    "SM-07": (DEFERRED, "missed-call text — carrier required"),
    "AI-01": (DEFERRED, "Voice AI — specialist vendor"),
    "AI-05": (DEFERRED, "authoring aid, not a running feature"),
    "RP-02": (DEFERRED, "call reporting — follows telephony"),
    "MC-01": (DEFERRED, "courses — aspireelearning.com"),
    "MC-02": (DEFERRED, "portal — aspireelearning.com"),
    "MC-03": (DEFERRED, "communities — verify usage"),
    "MC-04": (DEFERRED, "certificates — aspireelearning.com"),
    "AG-01": (DEFERRED, "audit scope question, not a feature"),
    "AG-02": (DEFERRED, "audit instrument, not a feature"),
    "AG-03": (DEFERRED, "SaaS mode — Aspire does not resell"),
    "FW-01": (DEFERRED, "funnels — CMS, verify what GHL actually hosts"),
    "FW-02": (DEFERRED, "websites — almost certainly not in GHL"),
    "FW-03": (DEFERRED, "order forms — commerce surface"),
    "FW-04": (DEFERRED, "domains — follows hosting"),
    "FW-05": (DEFERRED, "pixels — follows hosting"),
    "BL-01": (DEFERRED, "blogs — CMS"),
    "BL-02": (DEFERRED, "blog posts — CMS"),
    "BL-03": (DEFERRED, "blog taxonomy — CMS"),
    "ST-03": (DEFERRED, "URL redirects — follows hosting"),
    "ST-04": (DEFERRED, "DNS — follows hosting"),
    "MO-01": (DEFERRED, "no native mobile app in Twenty"),
}

# Which n8n workflow implements which features. A workflow that exists but has
# never run counts as BUILT, never LIVE.
WORKFLOW_FEATURES = {
    "sys-error-handler": ["WF-11"],
    "vend-send-email": ["EM-01", "DM-07", "EM-07"],
    "lead-form-intake": ["FS-01", "FS-02", "FS-04", "CT-04"],
    "msg-tracked-link-redirect": ["EM-05"],
    "sub-renewal-escalation": ["OP-02"],
    "ops-scheduled-sweeps": ["CL-05", "FS-05"],
}

# Twenty objects that hold a feature's data but have no automation yet.
OBJECT_FEATURES = {
    "messageLog": ["CV-07"], "callLog": [], "emailTemplate": ["EM-02"],
    "campaign": ["EM-03", "EM-04", "WF-10"], "trackedLink": [],
    "formDefinition": ["FS-03"], "appointment": ["CL-01", "CL-02", "CL-03", "CL-04"],
    "product": ["PY-01"], "quote": ["PY-03"], "invoice": ["PY-02"],
    "payment": ["PY-05", "PY-06"], "landingPage": [], "socialPost": ["SO-01", "SO-02"],
    "review": ["RV-01", "RV-02"], "membershipEnrollment": [],
    "crmTag": ["DM-05"], "mergeVariable": ["DM-04"],
    "serviceSubscription": ["PY-04"], "complianceEngagement": [],
    "trainingAccount": [], "phishingBaseline": [], "consentRecord": [],
    "automationRun": [], "quoteLineItem": [], "formSubmission": [],
}

# Features satisfied by the provisioned data model itself.
MODEL_FEATURES = ["DM-01", "DM-02", "DM-03", "DM-06", "OP-01"]


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


def http(url: str, headers: dict[str, str]) -> tuple[int, str]:
    req = request.Request(url, method="GET")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except error.HTTPError as e:
        return e.code, ""
    except Exception:  # noqa: BLE001
        return 0, ""


def _assert_no_asserted_live() -> None:
    """LIVE must be measured, never declared. See the note on EXPLICIT."""
    asserted = [f for f, (st, _) in EXPLICIT.items() if st == LIVE]
    if asserted:
        raise SystemExit(
            "EXPLICIT claims LIVE for " + ", ".join(sorted(asserted))
            + " — LIVE may only come from probing the running stack.")


def probe_live(env: dict[str, str]) -> tuple[set[str], dict[str, bool], str]:
    """Ask the running stack what exists. Returns (objects, workflow->ran, note)."""
    objects: set[str] = set()
    ran: dict[str, bool] = {}

    base = env.get("TWENTY_BASE_URL") or env.get("SERVER_URL") or "http://localhost:3000"
    key = env.get("TWENTY_API_KEY", "")
    if key:
        status, body = http(f"{base.rstrip('/')}/rest/metadata/objects",
                            {"Authorization": f"Bearer {key}"})
        if status == 200:
            try:
                for o in json.loads(body)["data"]["objects"]:
                    objects.add(o.get("nameSingular"))
            except Exception:  # noqa: BLE001
                pass

    n8n = env.get("N8N_BASE_URL") or env.get("N8N_PUBLIC_URL") or "http://localhost:5678"
    nkey = env.get("N8N_API_KEY", "")
    note = "stack not reachable — reporting from committed files only"
    if nkey:
        s1, wf_body = http(f"{n8n.rstrip('/')}/api/v1/workflows?limit=100",
                           {"X-N8N-API-KEY": nkey})
        s2, ex_body = http(f"{n8n.rstrip('/')}/api/v1/executions?limit=250",
                           {"X-N8N-API-KEY": nkey})
        if s1 == 200:
            note = "probed live stack"
            names = {w["id"]: w["name"] for w in json.loads(wf_body)["data"]}
            succeeded = set()
            if s2 == 200:
                for e in json.loads(ex_body)["data"]:
                    if e.get("status") == "success":
                        succeeded.add(names.get(e.get("workflowId"), ""))
            for wid, name in names.items():
                ran[name] = name in succeeded
    return objects, ran, note


def main() -> int:
    ap = argparse.ArgumentParser(description="Honest implementation status")
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args()

    _assert_no_asserted_live()
    env = read_env()
    live_objects, workflow_ran, note = probe_live(env)

    slug_to_name = {
        "sys-error-handler": "SYS Error Handler",
        "vend-send-email": "VEND Send Email",
        "lead-form-intake": "LEAD Form Intake",
        "msg-tracked-link-redirect": "MSG Tracked Link Redirect",
        "sub-renewal-escalation": "SUB Renewal Escalation",
        "ops-scheduled-sweeps": "OPS Scheduled Sweeps",
    }

    # Feature -> best status found
    status: dict[str, tuple[str, str]] = {}

    def claim(fid: str, st: str, why: str) -> None:
        if fid not in status or ORDER.index(st) < ORDER.index(status[fid][0]):
            status[fid] = (st, why)

    for fid in MODEL_FEATURES:
        claim(fid, LIVE if live_objects else BUILT, "provisioned object model")

    for obj, feats in OBJECT_FEATURES.items():
        present = obj in live_objects
        for fid in feats:
            claim(fid, MODELLED,
                  f"`{obj}` object {'exists' if present else 'defined'}, no automation")

    for slug, feats in WORKFLOW_FEATURES.items():
        exists = (WORKFLOW_DIR / f"{slug}.json").exists()
        proven = workflow_ran.get(slug_to_name.get(slug, ""), False)
        for fid in feats:
            if exists:
                claim(fid, LIVE if proven else BUILT,
                      f"workflow `{slug}`" + ("" if proven else " — never executed"))

    for fid, (st, why) in EXPLICIT.items():
        status[fid] = (st, why)

    rows = list(csv.DictReader(TAXONOMY.open(encoding="utf-8", newline="")))
    for r in rows:
        status.setdefault(r["id"], (DESIGNED, "specified in the guide only"))

    tally = {k: 0 for k in ORDER}
    for r in rows:
        tally[status[r["id"]][0]] += 1
    total = len(rows)

    if args.markdown:
        print("# Implementation Status\n")
        print(f"Source: {note}. {total} catalogued features.\n")
        print("| Status | Count | Meaning |\n|---|---|---|")
        meaning = {
            LIVE: "Verified working on the running stack",
            BUILT: "Code committed, **never executed**",
            NATIVE: "Twenty does it; needs configuration only",
            MODELLED: "Object exists to hold the data; no automation",
            DESIGNED: "Specified in the guide; nothing built",
            DEFERRED: "Deliberately out of scope, reason recorded",
            NOT_STARTED: "Not addressed",
        }
        for k in ORDER:
            if tally[k]:
                print(f"| {k} | {tally[k]} | {meaning[k]} |")
        print()
        area = None
        for r in rows:
            if r["area"] != area:
                area = r["area"]
                print(f"\n### {area}\n\n| ID | Feature | Status | Evidence |\n|---|---|---|---|")
            st, why = status[r["id"]]
            print(f"| `{r['id']}` | {r['feature']} | **{st}** | {why} |")
        return 0

    print(f"\n  {note}\n")
    area = None
    for r in rows:
        if r["area"] != area:
            area = r["area"]
            print(f"\n\033[1m{area}\033[0m")
        st, why = status[r["id"]]
        print(f"  {COLOUR[st]}{st:<11}{RESET} {r['id']:<6} {r['feature'][:38]:<40} {why[:44]}")

    print("\n" + "=" * 78)
    for k in ORDER:
        if tally[k]:
            pct = tally[k] * 100 / total
            print(f"  {COLOUR[k]}{k:<11}{RESET} {tally[k]:>3}  {pct:>5.1f}%  "
                  + "#" * int(pct / 2))
    print("=" * 78)

    working = tally[LIVE] + tally[NATIVE]
    print(f"\n  Working or native today : {working}/{total} ({working*100/total:.0f}%)")
    print(f"  Built but unproven      : {tally[BUILT]}  <- highest risk")
    print(f"  Still to build          : {tally[MODELLED] + tally[DESIGNED] + tally[NOT_STARTED]}")
    print(f"  Deliberately deferred   : {tally[DEFERRED]}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
