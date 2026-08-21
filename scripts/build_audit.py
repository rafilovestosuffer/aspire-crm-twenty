#!/usr/bin/env python3
"""
Merge the GHL pulls against the feature taxonomy into out/feature_audit.csv.

This script decides `in_use` from evidence on disk. It never guesses. A feature
whose evidence file is absent comes out as `unknown`, not `no` — the difference
matters, because `no` retires a feature and `unknown` means nobody has looked yet.

Usage:
    python scripts/build_audit.py
    python scripts/build_audit.py --min-records 1
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TAXONOMY = ROOT / "reference" / "ghl_feature_taxonomy.csv"
RAW = ROOT / "raw"
OUT = ROOT / "out"
WORKFLOWS = ROOT / "workflows"

COLUMNS = [
    "id", "ghl_area", "feature", "in_use", "evidence", "volume_90d",
    "criticality", "disposition", "twenty_component", "n8n_component",
    "effort_days", "notes",
]


def load_evidence(source: str) -> tuple[str, int | None, str]:
    """
    Return (state, count, detail) for one evidence source.

    A source may target a single histogram bucket:
        raw/conversations.json#type=TYPE_PHONE
    That distinction matters. An aggregate of 1,877 conversations is evidence
    that *the inbox* is used; it is not evidence that WhatsApp is used. Without
    per-channel targeting, every channel inherits the total and the replacement
    scope is overstated.
    """
    if not source or source == "ui-observation":
        return "unknown", None, "requires manual UI check"

    source, _, selector = source.partition("#")
    path = ROOT / source
    if not path.exists():
        return "unknown", None, f"{source} not pulled yet"

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return "unknown", None, f"{source} unreadable: {e}"

    if selector:
        field, _, wanted = selector.partition("=")
        hist = (data or {}).get("histograms", {}) if isinstance(data, dict) else {}
        if field not in hist:
            return "unknown", None, (
                f"{source} has no '{field}' breakdown — cannot separate this "
                "channel from the aggregate; check by hand")
        n = int(hist[field].get(wanted, 0))
        # The field exists, so absence from the histogram is real evidence.
        return ("yes" if n > 0 else "no"), n, f"{source} {field}={wanted} n={n}"

    if isinstance(data, dict) and data.get("_class") == "volume":
        n = int(data.get("count", 0))
        return ("yes" if n > 0 else "no"), n, f"{source} count={n}"

    if isinstance(data, list):
        n = len(data)
        return ("yes" if n > 0 else "no"), n, f"{source} n={n}"

    if isinstance(data, dict) and data:
        return "yes", 1, source

    return "no", 0, f"{source} empty"


def workflow_internals_present() -> int:
    if not WORKFLOWS.exists():
        return 0
    return len(list(WORKFLOWS.glob("*.json")))


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the feature audit CSV")
    ap.add_argument("--min-records", type=int, default=1,
                    help="records required to count a feature as in use (default 1)")
    args = ap.parse_args()

    if not TAXONOMY.exists():
        print(f"ERROR: {TAXONOMY} missing", file=sys.stderr)
        return 2

    OUT.mkdir(exist_ok=True)
    rows: list[dict] = []
    counts = {"yes": 0, "no": 0, "unknown": 0}
    wf_files = workflow_internals_present()

    with TAXONOMY.open(encoding="utf-8", newline="") as fh:
        for t in csv.DictReader(fh):
            source = (t.get("evidence_source") or "").strip()

            if (t.get("detection_method") or "").strip() == "session":
                if wf_files:
                    state, count, detail = "yes", wf_files, f"workflows/ n={wf_files}"
                else:
                    state, count, detail = "unknown", None, "workflow internals not captured yet"
            else:
                state, count, detail = load_evidence(source)

            if state == "yes" and count is not None and count < args.min_records:
                state = "no"

            counts[state] += 1
            hypothesis = (t.get("disposition_hypothesis") or "UNKNOWN").strip()
            # A feature with no usage is a DROP candidate regardless of hypothesis.
            disposition = "DROP" if state == "no" else hypothesis

            rows.append({
                "id": t["id"],
                "ghl_area": t["area"],
                "feature": t["feature"],
                "in_use": state,
                "evidence": detail,
                "volume_90d": "" if count is None else count,
                "criticality": "",           # human judgement — filled after review
                "disposition": disposition,
                "twenty_component": t.get("twenty_native", ""),
                "n8n_component": t.get("n8n_role", ""),
                "effort_days": "",           # human judgement — filled after review
                "notes": t.get("notes", ""),
            })

    dest = OUT / "feature_audit.csv"
    with dest.open("w", newline="", encoding="utf-8") as fh:
        # lineterminator: csv defaults to \r\n, which .gitattributes forbids for
        # *.csv here. Left at the default the committed report is permanently
        # "modified" in git and a trailing \r rides on the last column.
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    by_disp: dict[str, int] = {}
    for r in rows:
        by_disp[r["disposition"]] = by_disp.get(r["disposition"], 0) + 1

    print(f"Wrote {dest.relative_to(ROOT)} — {len(rows)} feature(s)\n")
    print("  In use:")
    for k in ("yes", "no", "unknown"):
        print(f"    {k:8} {counts[k]:>3}")
    print("\n  Disposition:")
    for k, v in sorted(by_disp.items(), key=lambda kv: -kv[1]):
        print(f"    {k:14} {v:>3}")

    if counts["unknown"]:
        print(f"\n  {counts['unknown']} feature(s) still unknown. These are the "
              "manual-check list — the audit is not finished until this is zero.")
    if not wf_files:
        print("  Workflow internals not captured. WF-02..WF-09 cannot be "
              "classified until they are.")
    print("\n  criticality and effort_days are deliberately blank. They are "
          "judgement calls and must be filled by a person, not inferred.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
