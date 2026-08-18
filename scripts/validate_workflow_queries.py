#!/usr/bin/env python3
"""
Check every Twenty API call the workflows make against the live schema.

This exists because of a specific, expensive class of bug: Twenty does not
reliably reject a malformed query. Send `?filter[email][eq]=x` — the syntax
n8n users reach for first — and Twenty ignores it and returns **the whole
table**. The workflow then happily uses row 0. Nothing errors. Nothing is
logged. In the run that prompted this script, a lead acknowledgement email was
sent to an unrelated contact, and the only reason anyone noticed was that the
name in the email was wrong.

So the rules below are not style preferences. Each one is a silent-wrong-data
failure that reached a live run:

    filter      filter=field[op]:value      NOT filter[field][op]=value
    in          filter=f[in]:[A,B]          bare comma list is a 400
    order       order_by=field[Desc...]     `orderBy` is silently ignored
    limit       limit=200 is the ceiling    500 returns 200 rows, HTTP 200
    fields      must exist on the object    unknown ones are ignored or 400
    enums       must be a declared option   wrong values 400 at runtime

Usage:
    python scripts/validate_workflow_queries.py
    python scripts/validate_workflow_queries.py --quiet     # exit code only
    python scripts/validate_workflow_queries.py --offline   # no stack needed

--offline checks against reference/twenty_schema.yaml rather than a running
instance. It cannot see drift or anything provisioned by hand, so it does not
replace the live run — but it catches the syntax and typo classes above on a
machine with nothing running, which is better than checking nothing at all.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / "n8n" / "workflows"

# "/rest/people?filter=..." out of "={{ $env.TWENTY_BASE_URL }}/rest/people?..."
REST_CALL = re.compile(r"/rest/([A-Za-z0-9_]+)(\?[^\"']*)?")
# filter=field[op]:value  — value runs to the next comma outside brackets
GOOD_FILTER = re.compile(r"([A-Za-z0-9_.]+)\[([A-Za-z]+)\]:")
# the wrong shape: filter[field][op]=
BAD_FILTER = re.compile(r"filter\[([A-Za-z0-9_.]+)\]\[([A-Za-z]+)\]=")
PLACEHOLDER = re.compile(r"\{\{.*?\}\}", re.S)


def read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for f in (ROOT / "infra" / ".env", ROOT / ".env"):
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env.setdefault(k.strip(), v.strip().strip("\"'"))
    for k in ("TWENTY_BASE_URL", "TWENTY_API_KEY"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


# Standard Twenty objects the workflows touch. They are not in
# reference/twenty_schema.yaml — that file deliberately defines only the custom
# layer — so offline mode would otherwise report every task and person write as
# an unknown object. Only the fields these workflows actually use are listed;
# offline mode is a syntax and typo net, not a substitute for the live check.
STANDARD_OBJECTS: dict[str, dict[str, dict]] = {
    "people": {f: {"type": None, "options": []} for f in (
        "id name emails phones companyId city jobTitle avatarUrl "
        "createdAt updatedAt deletedAt position").split()},
    "companies": {f: {"type": None, "options": []} for f in (
        "id name domainName address employees accountOwnerId "
        "createdAt updatedAt deletedAt position").split()},
    "opportunities": {f: {"type": None, "options": []} for f in (
        "id name closeDate amount companyId pointOfContactId "
        "createdAt updatedAt deletedAt position").split()} | {
        # Twenty ships these five and no WON. Getting this wrong is a runtime
        # 400 on a stage that looks perfectly reasonable in a diff.
        "stage": {"type": "SELECT",
                  "options": ["NEW", "SCREENING", "MEETING",
                              "PROPOSAL", "CUSTOMER"]}},
    "tasks": {f: {"type": None, "options": []} for f in (
        "id title bodyV2 dueAt assigneeId createdAt updatedAt "
        "deletedAt position").split()} | {
        "status": {"type": "SELECT",
                   "options": ["TODO", "IN_PROGRESS", "DONE"]}},
    "notes": {f: {"type": None, "options": []} for f in (
        "id title bodyV2 createdAt updatedAt deletedAt position").split()},
}


def option_value(label: str) -> str:
    """'Renewal Due' -> 'RENEWAL_DUE'. Mirrors scripts/twenty_provision.py.

    If these two ever disagree, offline validation passes and the live
    instance 400s — so the duplication is deliberate but must stay in step.
    """
    return re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_").upper() or "OPTION"


def load_schema_offline() -> dict[str, dict]:
    """Build the same shape as load_schema() from the version-controlled YAML.

    The live check is better and stays the default: it sees what was actually
    provisioned, including drift and anything added by hand. This exists so the
    generator's output can be checked on a machine with no stack running —
    during review, in CI, or before the first deploy — where the alternative is
    checking nothing at all.
    """
    try:
        import yaml  # type: ignore
    except ImportError:
        raise SystemExit("PyYAML is required for --offline: "
                         "pip install -r scripts/requirements.txt")

    doc = yaml.safe_load((ROOT / "reference" / "twenty_schema.yaml")
                         .read_text(encoding="utf-8"))
    schema: dict[str, dict] = {k: dict(v) for k, v in STANDARD_OBJECTS.items()}

    for o in doc.get("objects", []):
        fields: dict[str, dict] = {
            f: {"type": None, "options": []}
            for f in ("id createdAt updatedAt deletedAt position "
                      "name searchVector").split()
        }
        for f in o.get("fields", []):
            fields[f["name"]] = {
                "type": f.get("type"),
                "options": [option_value(x) for x in (f.get("options") or [])],
            }
        for r in o.get("relations", []):
            fields[r["to"] + "Id"] = {"type": "UUID", "options": []}
        schema[o["namePlural"]] = fields
    return schema


def load_schema(base: str, key: str) -> dict[str, dict]:
    """
    {namePlural: {field: {"type":..., "options":[...]}}} for the live instance.

    Two sources, because neither is complete on its own:

    - `/rest/metadata/objects` gives every object and every SELECT's options,
      but it embeds only a *slice* of each object's fields. The endpoint
      rejects every query string, so the remainder cannot be paged in — a
      validator built on it alone reports real fields as missing.
    - A sample row from `/rest/{object}?limit=1` carries the full key set,
      including composite ids like `companyId`, but says nothing about types
      and is empty for an object with no records yet.

    Merging them covers the gap in both directions.
    """
    req = request.Request(f"{base.rstrip('/')}/rest/metadata/objects", method="GET")
    req.add_header("Authorization", f"Bearer {key}")
    with request.urlopen(req, timeout=30) as r:
        body = json.loads(r.read().decode())
    node = body.get("data", body)
    objects = node.get("objects", node) if isinstance(node, dict) else node

    schema: dict[str, dict] = {}
    for o in objects:
        fields: dict[str, dict] = {}
        for f in o.get("fields", []):
            fields[f["name"]] = {
                "type": f.get("type"),
                "options": [x["value"] for x in (f.get("options") or [])],
            }
            # A relation is filtered by <name>Id, which is not itself listed.
            if f.get("type") == "RELATION":
                fields[f["name"] + "Id"] = {"type": "UUID", "options": []}
        schema[o["namePlural"]] = fields
    return schema


def enrich_from_sample(base: str, key: str, obj: str,
                       schema: dict[str, dict]) -> None:
    """Add any field names a real record has that the metadata slice omitted."""
    if obj not in schema:
        return
    req = request.Request(f"{base.rstrip('/')}/rest/{obj}?limit=1", method="GET")
    req.add_header("Authorization", f"Bearer {key}")
    try:
        with request.urlopen(req, timeout=20) as r:
            rows = json.loads(r.read().decode()).get("data", {}).get(obj, [])
    except (error.HTTPError, OSError):
        return
    if not rows:
        return
    for name in rows[0]:
        schema[obj].setdefault(name, {"type": None, "options": []})


def field_exists(base: str, key: str, obj: str, field: str) -> bool:
    """
    Ask the API directly whether a field can be filtered on.

    Twenty rejects an unknown field by name, and complains about the value
    shape for a known one — so the error text, not the status code, is the
    signal. The wording is not stable across objects (`does not have field X`
    vs `doesn't have any "X" field`), so match the shape of the sentence
    rather than one phrasing. Used only when neither metadata nor a sample row
    can settle it, so a validator gap never becomes a false accusation.
    """
    url = (f"{base.rstrip('/')}/rest/{obj}"
           f"?filter={parse.quote(field)}%5Beq%5D:x&limit=1")
    req = request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {key}")
    try:
        with request.urlopen(req, timeout=20):
            return True
    except error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace").lower()
        unknown = ("does not have" in msg or "doesn't have" in msg) and "field" in msg
        return not unknown
    except OSError:
        return True


def check_call(obj: str, query: str, schema: dict[str, dict],
               where: str, probe=None) -> list[str]:
    problems: list[str] = []
    if obj not in schema:
        return [f"{where}: unknown object `{obj}`"]
    fields = schema[obj]

    for m in BAD_FILTER.finditer(query):
        problems.append(
            f"{where}: `filter[{m.group(1)}][{m.group(2)}]=` is ignored by "
            f"Twenty — the whole table comes back. Use "
            f"`filter={m.group(1)}[{m.group(2)}]:value`")

    if "orderBy=" in query:
        problems.append(f"{where}: `orderBy=` is silently ignored — use `order_by=`")

    params = parse.parse_qs(query.lstrip("?"), keep_blank_values=True)

    # 200 is a hard ceiling, not a default. Ask for 500 and Twenty returns 200
    # rows with HTTP 200 and no warning of any kind, so a scan written this way
    # reports on a subset and looks entirely healthy doing it.
    for raw in params.get("limit", []):
        if raw.isdigit() and int(raw) > 200:
            problems.append(
                f"{where}: `limit={raw}` exceeds Twenty's ceiling — it returns "
                f"200 rows, HTTP 200, no warning. Use limit=200 and paginate.")

    for raw in params.get("filter", []):
        for fm in GOOD_FILTER.finditer(raw):
            name, op = fm.group(1), fm.group(2)
            root = name.split(".")[0]
            if root not in fields and not (probe and probe(obj, root)):
                problems.append(f"{where}: filters on `{name}`, which "
                                f"`{obj}` does not have")
                continue
            value = raw[fm.end():].split(",", 1)[0] if op != "in" else ""
            if op == "in":
                tail = raw[fm.end():]
                if not tail.startswith("["):
                    problems.append(f"{where}: `{name}[in]` needs a bracketed "
                                    f"array — `[A,B]` — a bare list is a 400")
                    continue
                value = tail[1:tail.find("]")] if "]" in tail else tail[1:]
            # `root` may have been confirmed by the live probe rather than
            # found in `fields` — that is the whole point of the probe — so it
            # is not necessarily a key here. Indexing it directly crashed the
            # gate on a fresh stack, where an object with no rows yet cannot be
            # enriched from a sample and the metadata slice omitted the field.
            opts = fields.get(root, {}).get("options") or []
            if opts and value and not PLACEHOLDER.search(value):
                for v in (x.strip() for x in value.split(",")):
                    if v and v not in opts:
                        problems.append(
                            f"{where}: `{name}` has no option `{v}` "
                            f"(valid: {', '.join(opts)})")

    for raw in params.get("order_by", []):
        name = raw.split("[")[0]
        if name and name not in fields and not (probe and probe(obj, name)):
            problems.append(f"{where}: orders by `{name}`, which "
                            f"`{obj}` does not have")
    return problems


def check_body(obj: str, body: str, schema: dict[str, dict],
               where: str, probe=None) -> list[str]:
    """Top-level keys of a write payload must be real fields on the object."""
    if obj not in schema:
        return []
    fields = schema[obj]
    problems = []
    # jsonBody is JS, not JSON — pull the quoted keys at brace depth 1.
    inner = body[body.find("stringify(") + 10:] if "stringify(" in body else body
    depth = 0
    for m in re.finditer(r'[{}]|"([A-Za-z0-9_]+)":', inner):
        if m.group(0) == "{":
            depth += 1
        elif m.group(0) == "}":
            depth -= 1
            if depth <= 0:
                break
        elif depth == 1 and m.group(1) not in fields and not (
                probe and probe(obj, m.group(1))):
            problems.append(f"{where}: writes `{m.group(1)}`, which "
                            f"`{obj}` does not have")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate workflow queries")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--offline", action="store_true",
                    help="check against reference/twenty_schema.yaml instead "
                         "of a live instance. Weaker — it cannot see drift or "
                         "anything provisioned by hand — but it runs anywhere.")
    args = ap.parse_args()

    env = read_env()
    base = env.get("TWENTY_BASE_URL", "http://localhost:3000")
    key = env.get("TWENTY_API_KEY", "")

    if args.offline:
        schema = load_schema_offline()
        probe = None
        source = "reference/twenty_schema.yaml (offline)"
    else:
        if not key:
            print("ERROR: TWENTY_API_KEY not set. Use --offline to check "
                  "against the schema file instead.", file=sys.stderr)
            return 2
        try:
            schema = load_schema(base, key)
        except (error.HTTPError, OSError) as e:
            print(f"ERROR: cannot read schema from {base} — {e}", file=sys.stderr)
            return 2
        source = base

        probe_cache: dict[tuple[str, str], bool] = {}

        def probe(obj: str, field: str) -> bool:  # type: ignore[misc]
            hit = probe_cache.get((obj, field))
            if hit is None:
                hit = field_exists(base, key, obj, field)
                probe_cache[(obj, field)] = hit
            return hit

    if not args.quiet:
        print(f"schema source: {source}")

    problems: list[str] = []
    calls = 0
    seen_objects: set[str] = set()
    for path in sorted(WORKFLOWS.glob("*.json")):
        flow = json.loads(path.read_text(encoding="utf-8"))
        for node in flow.get("nodes", []):
            params = node.get("parameters") or {}
            url = params.get("url")
            if not isinstance(url, str):
                continue
            m = REST_CALL.search(url)
            if not m:
                continue
            calls += 1
            obj, query = m.group(1), m.group(2) or ""
            if obj not in seen_objects:
                seen_objects.add(obj)
                if not args.offline:
                    enrich_from_sample(base, key, obj, schema)
            where = f"{flow['name']} / {node['name']}"
            problems += check_call(obj, query, schema, where, probe)
            body = params.get("jsonBody")
            if isinstance(body, str) and params.get("method") in ("POST", "PATCH"):
                problems += check_body(obj, body, schema, where, probe)

    if not args.quiet:
        print(f"Checked {calls} Twenty API call(s) against {source}\n")
        for p in problems:
            print(f"  {p}")
        if problems:
            print(f"\n{len(problems)} problem(s).")
        elif args.offline:
            # Never claim the live check passed when it did not run. The whole
            # point of this script is that Twenty fails silently, and a
            # validator that overstates its own coverage is the same bug.
            print("\nAll calls valid against the schema file. This does NOT "
                  "prove them against a running instance —\nre-run without "
                  "--offline once the stack is up.")
        else:
            print("\nAll calls valid against the live schema.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
