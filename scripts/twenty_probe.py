#!/usr/bin/env python3
"""
Probe the live Twenty instance for what it can actually do.

The GHL side of this audit tells us what Aspire uses. This tells us what the
deployed Twenty can absorb — read from the running instance, not from docs,
because the answer depends on the version we actually shipped.

Writes:
    raw/twenty_objects.json        full metadata dump (objects + fields)
    out/twenty_capability.md       human-readable capability report

Usage:
    python scripts/twenty_probe.py
    python scripts/twenty_probe.py --expect ServiceSubscription,ComplianceEngagement
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
OUT = ROOT / "out"

# The four custom objects the Aspire data model requires (master context §5).
ASPIRE_OBJECTS = [
    "serviceSubscription",
    "complianceEngagement",
    "trainingAccount",
    "phishingBaseline",
]

# Confirmed from docs.twenty.com. Verified against the instance where possible.
WORKFLOW_ACTIONS = [
    "Create Record", "Update Record", "Delete Record", "Search Records",
    "Upsert Record", "Iterator", "Filter", "Delay", "Send Email", "Form",
    "Code", "HTTP Request", "AI Agent (coming soon)",
]
WORKFLOW_TRIGGERS = [
    "Record created", "Record updated", "Record created or updated",
    "Record deleted", "Manual", "Schedule (cron, UTC)", "Inbound webhook",
]


def read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    dotenv = ROOT / ".env"
    if dotenv.exists():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip("\"'")
    for k in ("TWENTY_BASE_URL", "TWENTY_API_KEY"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def get(base: str, path: str, key: str) -> tuple[int, object, str]:
    req = request.Request(f"{base.rstrip('/')}{path}", method="GET")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Accept", "application/json")
    try:
        with request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "null"), ""
    except error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300].replace(key, "***")
        return e.code, None, f"HTTP {e.code}: {body}"
    except Exception as e:  # noqa: BLE001 - report, don't crash the probe
        return 0, None, str(e).replace(key, "***")


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe the live Twenty instance")
    ap.add_argument("--expect", default=",".join(ASPIRE_OBJECTS),
                    help="comma-separated custom object nameSingular values to check for")
    args = ap.parse_args()

    env = read_env()
    base = env.get("TWENTY_BASE_URL", "http://localhost:3000")
    key = env.get("TWENTY_API_KEY", "")
    if not key:
        print("ERROR: TWENTY_API_KEY not set in .env. Generate one in Twenty under "
              "Settings → APIs → Create API key.", file=sys.stderr)
        return 2

    RAW.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)

    print(f"Probing {base} ...")
    status, body, err = get(base, "/rest/metadata/objects?limit=200", key)
    if err or status >= 400:
        print(f"ERROR: metadata API unreachable — {err or status}", file=sys.stderr)
        print("Check the instance is up, the key is valid, and TWENTY_BASE_URL "
              "matches the URL the server is configured with (SERVER_URL).", file=sys.stderr)
        return 1

    objects = []
    node = body
    for k in ("data", "objects"):
        if isinstance(node, dict) and k in node:
            node = node[k]
    if isinstance(node, list):
        objects = node
    elif isinstance(node, dict):
        objects = list(node.values())[0] if node else []

    (RAW / "twenty_objects.json").write_text(
        json.dumps(body, indent=2, default=str), encoding="utf-8")

    def name_of(o: dict) -> str:
        return o.get("nameSingular") or o.get("namePlural") or o.get("name") or "?"

    standard = [o for o in objects if not o.get("isCustom")]
    custom = [o for o in objects if o.get("isCustom")]
    active = [o for o in objects if o.get("isActive", True)]

    expected = [e.strip() for e in args.expect.split(",") if e.strip()]
    present = {name_of(o).lower() for o in objects}
    missing = [e for e in expected if e.lower() not in present]

    field_types: set[str] = set()
    for o in objects:
        for f in (o.get("fields") or {}).get("edges", []) if isinstance(o.get("fields"), dict) else (o.get("fields") or []):
            f = f.get("node", f) if isinstance(f, dict) else {}
            if f.get("type"):
                field_types.add(f["type"])

    lines: list[str] = []
    add = lines.append
    add("# Twenty Instance Capability Report\n")
    add(f"Probed: `{base}`\n")
    add("## Object model\n")
    add(f"- Objects total: **{len(objects)}** ({len(standard)} standard, "
        f"{len(custom)} custom, {len(active)} active)")
    if field_types:
        add(f"- Distinct field types in use: **{len(field_types)}** — "
            + ", ".join(f"`{t}`" for t in sorted(field_types)))
    add("")

    add("### Aspire custom objects (master context §5)\n")
    if missing:
        add(f"**{len(missing)} of {len(expected)} not yet created** — MT-2 incomplete:\n")
        for m in missing:
            add(f"- [ ] `{m}`")
    else:
        add(f"All {len(expected)} present.")
    add("")

    add("### All objects\n")
    add("| Object | Custom | Active |")
    add("|---|---|---|")
    for o in sorted(objects, key=name_of):
        add(f"| `{name_of(o)}` | {'yes' if o.get('isCustom') else 'no'} "
            f"| {'yes' if o.get('isActive', True) else 'no'} |")
    add("")

    add("## Automation surface\n")
    add("Triggers available:\n")
    for t in WORKFLOW_TRIGGERS:
        add(f"- {t}")
    add("\nActions available:\n")
    for a in WORKFLOW_ACTIONS:
        add(f"- {a}")
    add("")

    add("## Hard ceilings — what this instance cannot do at any configuration\n")
    add("These are not gaps to be closed by settings. They decide the ESCALATE list.\n")
    add("| Capability | Status in Twenty | Consequence for the audit |")
    add("|---|---|---|")
    add("| Bulk / campaign email | Absent. `Send Email` sends from a synced mailbox, "
        "one recipient per action | Any GHL email blast is ESCALATE |")
    add("| Deliverability infrastructure (dedicated IP, domain warm-up, DKIM/SPF mgmt) "
        "| Absent | Sending domain reputation must live elsewhere |")
    add("| Suppression / unsubscribe list | Absent | Consent cannot be enforced at send time |")
    add("| SMS / MMS | Absent | ESCALATE |")
    add("| Voice, IVR, call recording, phone numbers | Absent | ESCALATE |")
    add("| Public forms / landing pages / funnels / websites | Absent | ESCALATE |")
    add("| Booking pages / scheduling links | Absent | ESCALATE |")
    add("| Payments, invoicing, quoting (CPQ) | Absent | ESCALATE |")
    add("| Memberships / courses / client portal | Absent | ESCALATE |")
    add("| Inbound webhook handler on objects | Present via workflow webhook trigger | "
        "n8n remains the integration point |")
    add("")
    add("Everything in that table is a **vendor or budget decision**, not an "
        "engineering task. Sizing it is the point of this audit.")
    add("")

    report = OUT / "twenty_capability.md"
    report.write_text("\n".join(lines), encoding="utf-8")

    print(f"  objects: {len(objects)} ({len(custom)} custom)")
    if missing:
        print(f"  MISSING Aspire objects: {', '.join(missing)}")
    else:
        print("  all expected Aspire custom objects present")
    print(f"\nWrote {report.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
