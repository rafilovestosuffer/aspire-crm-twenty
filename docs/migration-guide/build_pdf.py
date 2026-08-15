#!/usr/bin/env python3
"""
Assemble the GHL → Twenty + n8n replacement guide and render it to PDF.

Appendix A is generated from reference/ghl_feature_taxonomy.csv rather than
written by hand, so the guide and the audit instrument cannot drift apart.

Usage:
    python docs/migration-guide/build_pdf.py
"""

from __future__ import annotations

import csv
import html
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
TAXONOMY = ROOT / "reference" / "ghl_feature_taxonomy.csv"
OUT_PDF = ROOT / "out" / "GHL-to-Twenty-n8n-Replacement-Guide.pdf"

PARTS = ["part1.html", "part2.html", "part3.html", "part4.html", "part5.html",
         "part6.html", "part7.html", "part8.html"]
# Appendix A precedes the existing appendices, which historically began at B.
TAIL = "part9a.html"
TAIL2 = "part9.html"

# Phase 1 removes GoHighLevel entirely, so ESCALATE reads as VENDOR here.
PILL = {
    "TWENTY": ("t-twenty", "TWENTY"),
    "N8N": ("t-n8n", "N8N"),
    "TWENTY+N8N": ("t-both", "TWENTY+N8N"),
    "ESCALATE": ("t-vendor", "VENDOR"),
    "DROP-CANDIDATE": ("t-drop", "DROP?"),
    "DROP": ("t-drop", "DROP"),
    "UNKNOWN": ("t-drop", "TBC"),
}

# Where each taxonomy area is covered in the body of the guide.
SECTION_REF = {
    "Data Model": "§7", "Contacts & Segmentation": "§9",
    "Opportunities & Pipelines": "§8", "Conversations & Inbox": "§17, §19",
    "Email & Templates": "§18", "SMS & Telephony": "§19, §20",
    "Voice & AI Features": "§26", "Calendars & Scheduling": "§23",
    "Forms & Surveys": "§22", "Funnels & Websites": "§24",
    "Blogs & Content": "§24", "Workflows & Automation": "§11–16",
    "Payments & Commerce": "§25", "Memberships & Courses": "§28",
    "Reputation & Reviews": "§27", "Social & Ads": "§27",
    "Reporting & Dashboards": "§29", "Users & Permissions": "§10",
    "Settings & Business Profile": "§6, §24", "Integrations & Marketplace": "§30",
    "Agency & Multi-Location": "§30", "Media & Assets": "§7",
    "Mobile & Field Access": "§33",
}


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def build_appendix_a() -> str:
    if not TAXONOMY.exists():
        sys.exit(f"Missing {TAXONOMY}")

    rows: list[dict] = []
    with TAXONOMY.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    parts: list[str] = [
        '<h1 class="newpage">Appendices</h1>',
        '<h2>Appendix A · Complete feature disposition matrix</h2>',
        f"<p>All {len(rows)} catalogued GoHighLevel features across "
        f"{len({r['area'] for r in rows})} areas, with disposition and the section "
        "of this guide that specifies the replacement. Generated directly from "
        "<code>reference/ghl_feature_taxonomy.csv</code>, so it cannot drift from "
        "the audit instrument.</p>",
        '<div class="legend">'
        '<span class="pill t-twenty">TWENTY</span> Native &nbsp; '
        '<span class="pill t-n8n">N8N</span> n8n alone &nbsp; '
        '<span class="pill t-both">TWENTY+N8N</span> Record + logic &nbsp; '
        '<span class="pill t-vendor">VENDOR</span> Third party required &nbsp; '
        '<span class="pill t-drop">DROP?</span> Verify usage, likely retire</div>',
    ]

    tally: dict[str, int] = {}
    current_area = None

    for r in rows:
        area = r["area"]
        if area != current_area:
            if current_area is not None:
                parts.append("</table>")
            ref = SECTION_REF.get(area, "")
            parts.append(f'<h3>{esc(area)} <span class="small">— see {ref}</span></h3>'
                         if ref else f"<h3>{esc(area)}</h3>")
            parts.append('<table class="wide"><tr>'
                         '<th style="width:7%">ID</th>'
                         '<th style="width:30%">Feature</th>'
                         '<th style="width:14%">Disposition</th>'
                         '<th>Notes</th></tr>')
            current_area = area

        disp = (r.get("disposition_hypothesis") or "UNKNOWN").strip()
        cls, label = PILL.get(disp, ("t-drop", disp))
        tally[label] = tally.get(label, 0) + 1

        note = (r.get("notes") or "").strip()
        if len(note) > 175:
            note = note[:172].rsplit(" ", 1)[0] + "…"

        parts.append(
            f'<tr><td><code>{esc(r["id"])}</code></td>'
            f'<td>{esc(r["feature"])}</td>'
            f'<td><span class="pill {cls}">{label}</span></td>'
            f"<td>{esc(note)}</td></tr>")

    parts.append("</table>")

    summary = ['<h3>Totals</h3><table><tr><th style="width:26%">Disposition</th>'
               '<th style="width:14%">Count</th><th>Meaning</th></tr>']
    meaning = {
        "TWENTY": "Native Twenty. Configuration only.",
        "N8N": "n8n reproduces it with no Twenty involvement.",
        "TWENTY+N8N": "Record in Twenty, logic in n8n.",
        "VENDOR": "Third-party service required — consolidates into 8 decisions (§5).",
        "DROP?": "Likely unused or duplicated elsewhere. Verify, then retire.",
        "TBC": "Evidence row — resolved by the audit, not by this guide.",
    }
    for label in ("TWENTY", "N8N", "TWENTY+N8N", "VENDOR", "DROP?", "TBC"):
        if label in tally:
            cls = next(c for c, l in PILL.values() if l == label)
            summary.append(f'<tr><td><span class="pill {cls}">{label}</span></td>'
                           f'<td><b>{tally[label]}</b></td>'
                           f"<td>{meaning.get(label,'')}</td></tr>")
    summary.append("</table>")
    summary.append(
        '<div class="callout warn"><span class="lbl">This is a pre-audit upper bound</span>'
        "<p>Every feature GoHighLevel offers is counted, not every feature Aspire uses. "
        "The usage audit typically removes a third to a half of the surface, and four of "
        "the eight vendor clusters may collapse entirely once measured. Report this table "
        "as a ceiling, never as a workload.</p></div>")

    parts.extend(summary)
    return "\n".join(parts)


def build_toc(bookmarks, shift: int = 0) -> str:
    """
    Build the contents page from the rendered document's own bookmark tree.

    A 72-page document with hand-typed page numbers is wrong the moment a
    paragraph is edited. Taking the numbers from the render means the TOC is
    correct by construction.

    WeasyPrint bookmark entries are (label, (page_index, x, y), children, state)
    with 0-based page indices. `shift` accounts for the contents pages that this
    very TOC will insert ahead of the content.
    """
    rows = ['<div class="toc">', "<h1>Contents</h1>"]
    for label, _dest, children, _state in bookmarks:  # h1 = Part / Appendix
        rows.append(f'<div class="toc-part">{esc(label.strip())}</div>')
        for child_label, (page, _x, _y), _kids, _st in children:  # h2 = section
            rows.append(
                '<div class="toc-row">'
                f"<span>{esc(child_label.strip())}</span>"
                f"<span>{page + shift + 1}</span></div>")
    rows.append("</div>")
    return "\n".join(rows)


def assemble(toc_html: str) -> str:
    shell = (HERE / PARTS[0]).read_text(encoding="utf-8")
    body = [shell.partition("</body>")[0].replace("<!--TOC-->", toc_html)]
    for name in PARTS[1:]:
        body.append((HERE / name).read_text(encoding="utf-8"))
    body.append(build_appendix_a())
    body.append((HERE / TAIL).read_text(encoding="utf-8"))
    body.append((HERE / TAIL2).read_text(encoding="utf-8"))
    body.append("</body></html>")
    return "\n".join(body)


def main() -> int:
    try:
        from weasyprint import HTML
    except ImportError:
        sys.exit("WeasyPrint required:  pip install weasyprint")

    # Pass 1 — render without a contents page to discover where headings land.
    draft = HTML(string=assemble(""), base_url=str(HERE)).render()
    bookmarks = draft.make_bookmark_tree()

    # The contents page itself adds pages, shifting everything after it.
    # Measure by rendering cover + TOC alone, then offset every entry.
    cover_and_toc = (assemble(build_toc(bookmarks)).split("<h1>Part I")[0]
                     + "</body></html>")
    shift = len(HTML(string=cover_and_toc, base_url=str(HERE)).render().pages) - 1

    doc = assemble(build_toc(bookmarks, shift))
    (HERE / "_combined.html").write_text(doc, encoding="utf-8")

    OUT_PDF.parent.mkdir(exist_ok=True)
    final = HTML(string=doc, base_url=str(HERE)).render()
    final.write_pdf(OUT_PDF)

    size_kb = OUT_PDF.stat().st_size / 1024
    words = len(re.sub(r"<[^>]+>", " ", doc).split())
    print(f"Wrote {OUT_PDF.relative_to(ROOT)}")
    print(f"  {len(final.pages)} pages · {size_kb:.0f} KB · ~{words:,} words")
    print(f"  contents page occupies {shift} page(s); entries shifted to match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
