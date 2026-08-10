#!/usr/bin/env python3
"""
Provision the Aspire object model into Twenty via the Metadata API.

Builds the schema in reference/twenty_schema.yaml: 25 objects giving every GHL
domain a home in the CRM, including the ones Twenty cannot execute. Twenty holds
the record; n8n plus a vendor does the sending.

Idempotent — safe to re-run. Existing objects and fields are skipped, not
duplicated, so a partial run can simply be run again.

Three passes, in order, because relations cannot be created before both ends
exist:
    1. objects
    2. scalar fields
    3. relations

Usage:
    python scripts/twenty_provision.py --dry-run      # print every payload, send nothing
    python scripts/twenty_provision.py --objects-only
    python scripts/twenty_provision.py                # full provision
    python scripts/twenty_provision.py --only serviceSubscription,consentRecord
    python scripts/twenty_provision.py --skip-relations
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib import error, request

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "reference" / "twenty_schema.yaml"
OUT = ROOT / "out"

# Twenty's palette, cycled across SELECT options so pickers are readable.
COLORS = ["green", "turquoise", "sky", "blue", "purple",
          "pink", "red", "orange", "yellow", "gray"]

# Standard Twenty objects — referenced by relations, never created.
STANDARD = {"company", "person", "opportunity", "note", "task", "attachment"}

# Field icons by type. Cosmetic, but a 132-field workspace where every field
# carries the same icon is materially harder to scan.
TYPE_ICONS = {
    "TEXT": "IconAbc", "NUMBER": "IconNumbers", "BOOLEAN": "IconCheckbox",
    "DATE": "IconCalendar", "DATE_TIME": "IconClock", "CURRENCY": "IconCurrencyDollar",
    "SELECT": "IconSelect", "MULTI_SELECT": "IconListCheck", "LINKS": "IconLink",
    "RATING": "IconStar", "RAW_JSON": "IconCode", "EMAILS": "IconMail",
    "PHONES": "IconPhone", "ADDRESS": "IconMap", "ARRAY": "IconList",
}


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


def load_schema() -> list[dict]:
    text = SCHEMA.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
    except ImportError:
        raise SystemExit(
            "PyYAML is required for provisioning (the schema uses nested lists "
            "the fallback parser does not cover).\n"
            "  pip install -r scripts/requirements.txt")
    data = yaml.safe_load(text)
    return data.get("objects", [])


def option_value(label: str) -> str:
    """'Renewal Due' -> 'RENEWAL_DUE'. Twenty wants a stable machine value."""
    v = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_").upper()
    return v or "OPTION"


def build_options(labels: list[str]) -> list[dict]:
    return [{"label": lab,
             "value": option_value(lab),
             "position": i,
             "color": COLORS[i % len(COLORS)]}
            for i, lab in enumerate(labels)]


class Twenty:
    def __init__(self, base: str, key: str, dry_run: bool):
        self.base = base.rstrip("/")
        self.key = key
        self.dry_run = dry_run
        self.calls = 0

    def _request(self, method: str, path: str, body: dict | None = None
                 ) -> tuple[int, Any, str]:
        url = f"{self.base}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.key}")
        req.add_header("Accept", "application/json")
        if data:
            req.add_header("Content-Type", "application/json")
        self.calls += 1
        try:
            with request.urlopen(req, timeout=45) as resp:
                return resp.status, json.loads(resp.read().decode() or "null"), ""
        except error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")[:600].replace(self.key, "***")
            return e.code, None, f"HTTP {e.code}: {raw}"
        except Exception as e:  # noqa: BLE001
            return 0, None, str(e).replace(self.key, "***")

    def list_objects(self) -> dict[str, dict]:
        """Return {nameSingular: object} for everything already in the workspace."""
        status, body, err = self._request("GET", "/rest/metadata/objects?limit=200")
        if err or status >= 400:
            raise SystemExit(f"Cannot read metadata API — {err or status}\n"
                             "Check the instance is up, the key is valid, and "
                             "TWENTY_BASE_URL matches the server's SERVER_URL.")
        node = body
        for k in ("data", "objects"):
            if isinstance(node, dict) and k in node:
                node = node[k]
        items = node if isinstance(node, list) else []
        return {o.get("nameSingular"): o for o in items if isinstance(o, dict)}

    def existing_fields(self, obj: dict) -> set[str]:
        raw = obj.get("fields")
        if isinstance(raw, dict):
            raw = [e.get("node", e) for e in raw.get("edges", [])]
        return {f.get("name") for f in (raw or []) if isinstance(f, dict)}

    def create_object(self, spec: dict) -> tuple[bool, str]:
        payload = {
            "nameSingular": spec["nameSingular"],
            "namePlural": spec["namePlural"],
            "labelSingular": spec["labelSingular"],
            "labelPlural": spec["labelPlural"],
            "description": " ".join(str(spec.get("description", "")).split())[:900],
            "icon": spec.get("icon", "IconListDetails"),
        }
        if self.dry_run:
            print(f"    POST /rest/metadata/objects {json.dumps(payload)[:150]}...")
            return True, ""
        status, _, err = self._request("POST", "/rest/metadata/objects", payload)
        return (status < 400), err

    def create_field(self, object_id: str, spec: dict) -> tuple[bool, str]:
        payload: dict[str, Any] = {
            "objectMetadataId": object_id,
            "name": spec["name"],
            "label": spec["label"],
            "type": spec["type"],
            "icon": spec.get("icon") or TYPE_ICONS.get(spec["type"], "IconAbc"),
        }
        if spec.get("options"):
            payload["options"] = build_options(spec["options"])
        if self.dry_run:
            print(f"      POST /rest/metadata/fields {json.dumps(payload)[:150]}...")
            return True, ""
        status, _, err = self._request("POST", "/rest/metadata/fields", payload)
        return (status < 400), err

    def create_relation(self, source_id: str, target_id: str, rel: dict,
                        target_icon: str) -> tuple[bool, str]:
        payload = {
            "objectMetadataId": source_id,
            "name": rel["to"],
            "label": rel["label"],
            "type": "RELATION",
            "icon": "IconLink",
            "relationCreationPayload": {
                "targetObjectMetadataId": target_id,
                "targetFieldLabel": rel["reverseLabel"],
                "targetFieldIcon": target_icon,
                "type": rel.get("type", "MANY_TO_ONE"),
            },
        }
        if self.dry_run:
            print(f"      POST /rest/metadata/fields (RELATION) "
                  f"{rel['to']} <- {rel['label']}")
            return True, ""
        status, _, err = self._request("POST", "/rest/metadata/fields", payload)
        return (status < 400), err


def main() -> int:
    ap = argparse.ArgumentParser(description="Provision the Aspire model into Twenty")
    ap.add_argument("--dry-run", action="store_true", help="print payloads, send nothing")
    ap.add_argument("--objects-only", action="store_true")
    ap.add_argument("--skip-relations", action="store_true")
    ap.add_argument("--only", default="", help="comma-separated nameSingular values")
    args = ap.parse_args()

    specs = load_schema()
    wanted = {s.strip() for s in args.only.split(",") if s.strip()}
    if wanted:
        specs = [s for s in specs if s["nameSingular"] in wanted]

    n_fields = sum(len(s.get("fields", [])) for s in specs)
    n_rels = sum(len(s.get("relations", [])) for s in specs)
    print(f"Schema: {len(specs)} object(s), {n_fields} field(s), {n_rels} relation(s)\n")

    env = read_env()
    base = env.get("TWENTY_BASE_URL", "http://localhost:3000")
    key = env.get("TWENTY_API_KEY", "")

    if args.dry_run and not key:
        key = "DRY-RUN"
    if not key:
        print("ERROR: TWENTY_API_KEY not set in .env "
              "(Twenty → Settings → APIs → Create API key).", file=sys.stderr)
        return 2

    api = Twenty(base, key, args.dry_run)
    existing = {} if args.dry_run else api.list_objects()
    if not args.dry_run:
        print(f"Connected to {base} — {len(existing)} object(s) already present\n")

    report: list[dict] = []
    failures = 0

    # ---- Pass 1: objects ----
    print("Pass 1 — objects")
    for spec in specs:
        name = spec["nameSingular"]
        if name in existing:
            print(f"  have  {name}")
            report.append({"kind": "object", "name": name, "result": "existed"})
            continue
        print(f"  create {name}")
        ok, err = api.create_object(spec)
        report.append({"kind": "object", "name": name,
                       "result": "created" if ok else f"FAILED {err[:160]}"})
        if not ok:
            failures += 1
            print(f"         FAILED  {err[:200]}")

    if args.objects_only:
        print("\n--objects-only: stopping before fields.")
        return 0 if not failures else 1

    # Re-read so newly created objects carry their ids.
    if not args.dry_run:
        existing = api.list_objects()

    # ---- Pass 2: scalar fields ----
    print("\nPass 2 — fields")
    for spec in specs:
        name = spec["nameSingular"]
        obj = existing.get(name)
        if not obj and not args.dry_run:
            print(f"  skip  {name} (object missing — earlier failure)")
            continue
        have = api.existing_fields(obj) if obj else set()
        obj_id = (obj or {}).get("id", "DRY-RUN-ID")

        for f in spec.get("fields", []):
            if f["name"] in have:
                continue
            ok, err = api.create_field(obj_id, f)
            report.append({"kind": "field", "name": f"{name}.{f['name']}",
                           "result": "created" if ok else f"FAILED {err[:160]}"})
            if not ok:
                failures += 1
                print(f"    FAILED {name}.{f['name']}  {err[:180]}")
        print(f"  done  {name} ({len(spec.get('fields', []))} field(s))")

    # ---- Pass 3: relations ----
    if args.skip_relations:
        print("\n--skip-relations: stopping.")
    else:
        print("\nPass 3 — relations")
        if not args.dry_run:
            existing = api.list_objects()
        for spec in specs:
            name = spec["nameSingular"]
            obj = existing.get(name)
            if not obj and not args.dry_run:
                continue
            have = api.existing_fields(obj) if obj else set()
            src_id = (obj or {}).get("id", "DRY-RUN-ID")

            for rel in spec.get("relations", []):
                target = rel["to"]
                if target in have:
                    continue
                tgt = existing.get(target)
                if not tgt and not args.dry_run:
                    msg = (f"target '{target}' not found"
                           + (" — standard object may be named differently on "
                              "this version" if target in STANDARD else ""))
                    report.append({"kind": "relation",
                                   "name": f"{name}.{target}", "result": f"SKIPPED {msg}"})
                    print(f"    skip  {name}.{target}  {msg}")
                    continue
                ok, err = api.create_relation(
                    src_id, (tgt or {}).get("id", "DRY-RUN-ID"), rel,
                    spec.get("icon", "IconLink"))
                report.append({"kind": "relation", "name": f"{name}.{target}",
                               "result": "created" if ok else f"FAILED {err[:160]}"})
                if not ok:
                    failures += 1
                    print(f"    FAILED {name}.{target}  {err[:180]}")
            print(f"  done  {name} ({len(spec.get('relations', []))} relation(s))")

    # ---- Report ----
    if not args.dry_run:
        OUT.mkdir(exist_ok=True)
        dest = OUT / "twenty_provision_report.json"
        dest.write_text(json.dumps(report, indent=2), encoding="utf-8")
        created = sum(1 for r in report if r["result"] == "created")
        existed = sum(1 for r in report if r["result"] == "existed")
        print(f"\ncreated {created} · already present {existed} · failed {failures} "
              f"· {api.calls} API call(s)")
        print(f"Report: {dest.relative_to(ROOT)}")

    if failures:
        print("\nFailures above carry the API's own error text. Metadata payload "
              "shapes vary between Twenty versions — send me the error and the "
              "schema gets one edit, not a rewrite.", file=sys.stderr)
        return 1

    if args.dry_run:
        print("\nDry run — nothing sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
