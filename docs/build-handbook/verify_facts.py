#!/usr/bin/env python3
"""
Re-derive every number the handbook states, and fail if any document disagrees.

Prose drifts from source silently. The object model grew from 25 objects to 31
and four documents went on saying 25 for weeks — each of them individually
plausible, all of them wrong, and nothing in the build noticed. A reader
following a handbook that says "25 objects" against an instance that has 31 has
no way to tell which is broken.

So no count in the handbook is typed. Every one is computed here from the file
that actually defines it, and every document that repeats a count is checked
against the computed value. A schema change that is not reflected in the prose
fails this script, and this script runs in CI.

Usage:
    python3 docs/build-handbook/verify_facts.py
    python3 docs/build-handbook/verify_facts.py --json     # the facts, for tooling
    python3 docs/build-handbook/verify_facts.py --quiet    # exit code only
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA = ROOT / "reference" / "twenty_schema.yaml"
TAXONOMY = ROOT / "reference" / "ghl_feature_taxonomy.csv"
ENDPOINTS = ROOT / "reference" / "ghl_endpoints.yaml"
WORKFLOWS = ROOT / "n8n" / "workflows"
STATUS = ROOT / "out" / "implementation_status.md"

G, R, Y, D, B, O = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"

# Workflows that exist only to test the others. n8n_deploy.py refuses to push
# these without --dev, so they are never part of a production deployment.
DEV_ONLY = {"SYS Alert Sink (dev)", "SYS Failure Probe (dev)"}


# --------------------------------------------------------------------------
# Derive
# --------------------------------------------------------------------------

def facts() -> dict[str, int | str]:
    import yaml

    f: dict[str, int | str] = {}

    # ---- the object model -------------------------------------------------
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    objects = schema["objects"]
    f["objects"] = len(objects)
    f["fields"] = sum(len(o.get("fields") or []) for o in objects)
    f["relations"] = sum(len(o.get("relations") or []) for o in objects)
    # What the provisioner actually creates: one API call per field and per
    # relation. This is the number its own summary line reports back.
    f["field_definitions"] = f["fields"] + f["relations"]
    f["select_options"] = sum(
        len(fl.get("options") or [])
        for o in objects
        for fl in (o.get("fields") or [])
    )

    # ---- the workflow library --------------------------------------------
    wfs = sorted(WORKFLOWS.glob("*.json"))
    names, nodes, sticky = [], 0, 0
    for p in wfs:
        d = json.loads(p.read_text(encoding="utf-8"))
        names.append(d["name"])
        for n in d.get("nodes", []):
            nodes += 1
            if n["type"].endswith("stickyNote"):
                sticky += 1
    f["workflows"] = len(wfs)
    f["workflows_production"] = len([n for n in names if n not in DEV_ONLY])
    f["workflows_dev"] = len([n for n in names if n in DEV_ONLY])
    f["workflow_nodes"] = nodes
    # Sticky notes are documentation drawn on the canvas, not executable steps.
    # Quoting the raw total as "steps" overstates the automation by a third.
    f["workflow_nodes_executable"] = nodes - sticky

    # The generator is the source of truth for how many workflows should exist;
    # a builder removed from BUILDERS but left on disk is a real defect.
    gen = (ROOT / "scripts" / "build_n8n_workflows.py").read_text(encoding="utf-8")
    block = gen.partition("BUILDERS = [")[2].partition("]")[0]
    f["workflow_builders"] = len(re.findall(r"\bwf_\w+", block))

    # ---- the feature taxonomy --------------------------------------------
    rows = list(csv.DictReader(TAXONOMY.open(encoding="utf-8-sig", newline="")))
    f["features"] = len(rows)
    f["feature_areas"] = len({r["area"] for r in rows})
    for disp, n in Counter(r["disposition_hypothesis"] for r in rows).items():
        f[f"disposition_{disp.lower().replace('-', '_').replace('+', '_')}"] = n

    # ---- the endpoint registry -------------------------------------------
    reg = yaml.safe_load(ENDPOINTS.read_text(encoding="utf-8"))
    f["endpoints"] = len(reg["endpoints"])

    # ---- the gated build sequences ---------------------------------------
    for key, path in (("build_steps", "infra/rebuild.sh"),
                      ("deploy_steps", "infra/deploy.sh")):
        text = (ROOT / path).read_text(encoding="utf-8")
        m = re.search(r"^TOTAL=(\d+)", text, re.M)
        f[key] = int(m.group(1)) if m else 0
        # A TOTAL that disagrees with the number of run() calls means the
        # progress counter lies — "[9/11]" printed by the last step.
        f[key + "_actual"] = len(re.findall(r"^run ", text, re.M))

    # ---- measured against the running stack ------------------------------
    # These two cannot be derived from a file: they are what the scripts
    # themselves reported on a real run. Pinned here so that every document
    # repeating them agrees, and so that a change in either is a deliberate
    # edit to this line rather than prose quietly drifting.
    f["proof_checks"] = 77
    f["validated_api_calls"] = 91

    # ---- measured implementation status ----------------------------------
    if STATUS.exists():
        text = STATUS.read_text(encoding="utf-8")
        for level in ("LIVE", "NATIVE", "MODELLED", "DESIGNED", "DEFERRED"):
            m = re.search(rf"^\|\s*{level}\s*\|\s*(\d+)\s*\|", text, re.M)
            if m:
                f["status_" + level.lower()] = int(m.group(1))

    return f


# --------------------------------------------------------------------------
# Check the prose against it
# --------------------------------------------------------------------------
#
# (path, human label, regex with ONE capturing group, fact key)
#
# The regex must capture the number as the document writes it. A document that
# does not contain the pattern at all is not a failure — not every document
# quotes every count. A document that contains it with the wrong number is.

ASSERTIONS: list[tuple[str, str, str, str]] = [
    ("README.md", "object count",
     r"(\d+) objects, \d+ fields", "objects"),
    ("README.md", "field count",
     r"\d+ objects, (\d+) fields", "fields"),
    ("README.md", "schema summary",
     r"(\d+)-object GHL parity model", "objects"),
    ("README.md", "workflow library",
     r"(\d+) workflows — \d+ real, \d+ dev-only", "workflows"),
    ("README.md", "production workflows",
     r"\d+ workflows — (\d+) real, \d+ dev-only", "workflows_production"),
    ("README.md", "taxonomy size",
     r"(\d+)-feature master checklist", "features"),
    ("README.md", "endpoint registry",
     r"(\d+) read-only endpoints", "endpoints"),

    ("docs/06-twenty-object-model.md", "object count",
     r"\*\*(\d+) custom objects", "objects"),
    ("docs/06-twenty-object-model.md", "field count",
     r"custom objects, (\d+) fields", "fields"),
    ("docs/06-twenty-object-model.md", "relation count",
     r"fields, (\d+) relations", "relations"),

    ("docs/08-local-build.md", "object count",
     r"^(\d+) objects, \d+ fields, \d+ relations", "objects"),
    ("docs/08-local-build.md", "field count",
     r"^\d+ objects, (\d+) fields, \d+ relations", "fields"),
    ("docs/08-local-build.md", "relation count",
     r"^\d+ objects, \d+ fields, (\d+) relations", "relations"),

    ("docs/09-status-and-handover.md", "custom objects",
     r"\*\*(\d+) custom\*\*", "objects"),
    ("docs/09-status-and-handover.md", "field definitions",
     r"\*\*(\d+)\*\*, from a version-controlled schema", "field_definitions"),

    ("README.md", "proof-suite size",
     r"(\d+) checks against the running stack", "proof_checks"),
    ("docs/07-build-plan.md", "proof-suite size",
     r"(\d+) checks against the running stack", "proof_checks"),
    ("docs/08-local-build.md", "proof-suite size",
     r"whether the records exist\. (\d+) checks", "proof_checks"),
    ("docs/07-build-plan.md", "validated API calls",
     r"checks all (\d+) against the live schema", "validated_api_calls"),

    # The handbook states each of these repeatedly; all of them are checked.
    ("docs/build-handbook/part0.html", "object count",
     r"<b>(\d+) custom objects</b>", "objects"),
    ("docs/build-handbook/part0.html", "workflow count",
     r"<b>(\d+) workflows</b>", "workflows"),
    ("docs/build-handbook/part0.html", "proof-suite size",
     r"<b>(\d+) checks</b>", "proof_checks"),
    ("docs/build-handbook/part2.html", "provisioned totals",
     r"<b>(\d+)\s+objects\s+\+\s+\d+\s+fields", "objects"),
    ("docs/build-handbook/part2.html", "field total",
     r"<b>\d+\s+objects\s+\+\s+(\d+)\s+fields", "fields"),
    ("docs/build-handbook/part2.html", "relation total",
     r"fields\s+\+\s+(\d+)\s+relations\s+=", "relations"),
    ("docs/build-handbook/part2.html", "proof-suite size",
     r"<code>(\d+)/\d+ checks passed</code>", "proof_checks"),
    ("docs/build-handbook/part4.html", "validated API calls",
     r"All <b>(\d+)</b> API calls", "validated_api_calls"),
    ("docs/build-handbook/part4.html", "proof-suite size",
     r"<b>(\d+) checks\.</b>", "proof_checks"),
    ("docs/build-handbook/part5.html", "validated API calls",
     r"All (\d+), against this instance", "validated_api_calls"),
]

# Strings that were true once and are not any more. Cheap to check, and they
# catch the case where a count was reworded rather than corrected.
FORBIDDEN: list[tuple[str, str]] = [
    (r"25 objects, 132 fields", "superseded object-model counts"),
    (r"\b6 workflows, 58 nodes\b", "superseded workflow counts"),
]

SEARCH_GLOBS = ("README.md", "docs/*.md", "docs/build-handbook/*.html")


def check() -> tuple[list[str], list[str], dict]:
    f = facts()
    failures: list[str] = []
    passes: list[str] = []

    for rel, label, pattern, key in ASSERTIONS:
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        found = re.findall(pattern, text, re.M)
        if not found:
            continue                      # this document does not make the claim
        want = str(f[key])
        for got in found:
            if got != want:
                failures.append(f"{rel}: {label} says {got}, source says {want}")
            else:
                passes.append(f"{rel}: {label} = {got}")

    for pattern, why in FORBIDDEN:
        for glob in SEARCH_GLOBS:
            for path in sorted(ROOT.glob(glob)):
                for i, line in enumerate(
                        path.read_text(encoding="utf-8").splitlines(), 1):
                    if re.search(pattern, line):
                        failures.append(
                            f"{path.relative_to(ROOT)}:{i}: {why} — {line.strip()[:70]}")

    # Internal consistency: things that must agree with each other regardless
    # of what any document says.
    if f["workflows"] != f["workflow_builders"]:
        failures.append(
            f"n8n/workflows/ holds {f['workflows']} files but BUILDERS lists "
            f"{f['workflow_builders']} — regenerate with build_n8n_workflows.py")
    for key in ("build_steps", "deploy_steps"):
        if f[key] != f[key + "_actual"]:
            failures.append(
                f"{key}: TOTAL={f[key]} but {f[key + '_actual']} steps are run "
                f"— the progress counter would lie")

    return passes, failures, f


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="print the facts as JSON")
    ap.add_argument("--quiet", action="store_true", help="exit code only")
    args = ap.parse_args()

    passes, failures, f = check()

    if args.json:
        print(json.dumps(f, indent=2, sort_keys=True))
        return 1 if failures else 0

    if not args.quiet:
        print(f"{B}Facts derived from source{O}\n")
        width = max(len(k) for k in f)
        for k, v in sorted(f.items()):
            print(f"  {D}{k:<{width}}{O}  {v}")
        print(f"\n{B}Documents checked against them{O}\n")
        for p in passes:
            print(f"  {G}ok{O}    {p}")
        for x in failures:
            print(f"  {R}FAIL{O}  {x}")
        print()

    if failures:
        if not args.quiet:
            print(f"{R}{len(failures)} document(s) disagree with the source.{O}")
        return 1
    if not args.quiet:
        print(f"{G}Every stated count matches the file that defines it.{O}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
