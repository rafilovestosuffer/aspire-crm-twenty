#!/usr/bin/env python3
"""
Render docs/RUNBOOK.md to a paginated PDF in the house style.

The runbook is the source of truth; this script only typesets it, so the PDF
can never say something the markdown does not. Re-run it after any edit to
docs/RUNBOOK.md.

Usage:
    python3 docs/build_runbook_pdf.py
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = HERE / "RUNBOOK.md"
OUT_PDF = ROOT / "out" / "Aspire-CRM-Master-Runbook.pdf"

RUNNING_HEAD = "Aspire CRM · Master Runbook · GoHighLevel → Twenty + n8n"
FOOT_LEFT = "Aspire Tech · Confidential — Internal"

# Lifted from docs/migration-guide/part1.html so the two documents sit together
# on a desk without looking like they came from different companies.
CSS = """
@page {
  size: A4; margin: 20mm 17mm 18mm 17mm;
  @top-center { content: "__HEAD__"; font-size: 7.5pt; color: #8a94a6; padding-bottom: 3mm; }
  @bottom-right { content: counter(page); font-size: 8.5pt; color: #5a6478; }
  @bottom-left { content: "__FOOT__"; font-size: 7.5pt; color: #8a94a6; }
}
@page :first { @top-center { content: ""; } @bottom-left { content: ""; } @bottom-right { content: ""; } }

body { font-family: "DejaVu Sans", "Liberation Sans", sans-serif; font-size: 9.4pt;
       line-height: 1.5; color: #1c2430; margin: 0; }

h1 { bookmark-level: 1; bookmark-label: content(); font-size: 19pt; color: #12325a;
     margin: 0 0 3mm 0; line-height: 1.2; page-break-after: avoid; }
h2 { bookmark-level: 1; bookmark-label: content(); font-size: 15pt; color: #12325a;
     margin: 0 0 3.5mm 0; padding-bottom: 1.8mm; border-bottom: 1.8pt solid #12325a;
     page-break-before: always; page-break-after: avoid; line-height: 1.25; }
h2.nobreak { page-break-before: auto; }
h3 { bookmark-level: 2; bookmark-label: content(); font-size: 11pt; color: #1c4a80;
     margin: 6mm 0 2mm 0; page-break-after: avoid; }
h4 { bookmark-level: 3; bookmark-label: content(); font-size: 9.8pt; color: #24344a;
     margin: 4.5mm 0 1.5mm 0; page-break-after: avoid; }

p { margin: 0 0 2.5mm 0; text-align: justify; }
ul, ol { margin: 0 0 2.5mm 0; padding-left: 5.5mm; }
li { margin-bottom: 1.2mm; }
hr { border: none; border-top: 0.5pt solid #d5dde8; margin: 5mm 0; }
strong { color: #12325a; }
em { color: #24344a; }
a { color: #1c4a80; text-decoration: none; }

code { font-family: "DejaVu Sans Mono", monospace; font-size: 8.2pt; background: #eef2f7;
       padding: 0.3mm 1mm; border-radius: 1.5px; color: #0f3d6e; }
pre { font-family: "DejaVu Sans Mono", monospace; font-size: 7.8pt; background: #f5f8fb;
      border-left: 2.5pt solid #2d6cb5; padding: 2.5mm 3mm; margin: 2.5mm 0;
      white-space: pre-wrap; line-height: 1.35; page-break-inside: avoid; }
pre code { background: none; padding: 0; font-size: 7.8pt; color: #1c2430; }

table { border-collapse: collapse; width: 100%; margin: 2.5mm 0 4mm 0; font-size: 8.3pt;
        page-break-inside: auto; }
tr { page-break-inside: avoid; }
th { background: #12325a; color: #fff; text-align: left; padding: 1.8mm 2mm;
     font-weight: bold; font-size: 8.2pt; }
td { border-bottom: 0.5pt solid #d5dde8; padding: 1.6mm 2mm; vertical-align: top; }
tr:nth-child(even) td { background: #f7fafd; }
td strong { color: #12325a; }

blockquote { border-left: 3pt solid #b8860b; background: #fdf8ec; padding: 3mm 3.5mm;
             margin: 3.5mm 0; page-break-inside: avoid; }
blockquote p { margin-bottom: 1.5mm; text-align: left; }
blockquote p:last-child { margin-bottom: 0; }

.box { display: inline-block; width: 2.6mm; height: 2.6mm; border: 0.7pt solid #5a6478;
       margin-right: 1.6mm; vertical-align: -0.2mm; }
li.task { list-style: none; margin-left: -3.5mm; }

.cover { page-break-after: always; padding-top: 34mm; }
.cover .eyebrow { font-size: 10pt; color: #2d6cb5; letter-spacing: 2.5pt;
                  text-transform: uppercase; margin-bottom: 6mm; }
.cover .rule { height: 3pt; background: #2d6cb5; width: 52mm; margin-bottom: 8mm; }
.cover h1 { bookmark-level: none; font-size: 31pt; line-height: 1.14; margin-bottom: 5mm;
            border: none; }
.cover .sub { font-size: 12.5pt; color: #48566c; margin-bottom: 18mm; line-height: 1.45; }
.cover .meta { font-size: 9pt; color: #48566c; line-height: 1.85;
               border-top: 0.5pt solid #c8d2e0; padding-top: 5mm; }
.cover .meta b { color: #12325a; }
.cover .warnstrip { margin-top: 12mm; border-left: 3pt solid #a02c2c; background: #fbf0f0;
                    padding: 3mm 3.5mm; font-size: 8.6pt; color: #6b1f1f; }

.toc { page-break-after: always; }
.toc h1 { border: none; font-size: 17pt; margin-bottom: 1mm; bookmark-level: none; }
.toc .lede { font-size: 8.6pt; color: #5a6478; margin-bottom: 4mm; }
.toc-row { display: flex; justify-content: space-between; font-size: 8.9pt;
           margin-bottom: 0.6mm; border-bottom: 0.4pt dotted #d5dde8; padding-bottom: 0.6mm; }
.toc-row.sub { font-size: 8.1pt; padding-left: 6mm; border-bottom: none; color: #48566c;
               margin-bottom: 0.15mm; padding-bottom: 0; }
.toc-row span:first-child { color: #12325a; font-weight: bold; }
.toc-row.sub span:first-child { color: #48566c; font-weight: normal; }
.toc-row span:last-child { color: #8a94a6; font-weight: normal; }
""".replace("__HEAD__", RUNNING_HEAD).replace("__FOOT__", FOOT_LEFT)


def markdown_to_html(md: str) -> str:
    try:
        import markdown
    except ImportError:
        sys.exit("markdown required:  pip install markdown")
    return markdown.markdown(
        md,
        extensions=["tables", "fenced_code", "sane_lists", "attr_list"],
        output_format="html5",
    )


def prepare_source(md: str) -> tuple[str, dict[str, str]]:
    """Strip the title and the metadata table off the top; they go on the cover."""
    lines = md.split("\n")
    meta: dict[str, str] = {}
    for line in lines[:40]:
        m = re.match(r"^\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|$", line)
        if m and m.group(1) not in ("", "---") and not set(m.group(1)) <= {"-", " "}:
            meta.setdefault(m.group(1), m.group(2))
    # Body starts at the first horizontal rule after the metadata block.
    start = next(i for i, l in enumerate(lines) if l.strip() == "---" and i > 5)
    return "\n".join(lines[start + 1:]), meta


def strip_md(s: str) -> str:
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"\*\*([^*]*)\*\*", r"\1", s)
    return s.strip()


def build_cover(meta: dict[str, str]) -> str:
    def m(k: str, default: str = "") -> str:
        return html.escape(strip_md(meta.get(k, default)))

    return f"""
<div class="cover">
  <div class="eyebrow">Aspire Tech · CRM Migration Programme</div>
  <div class="rule"></div>
  <h1>Master Runbook</h1>
  <div class="sub">GoHighLevel → Twenty&nbsp;+&nbsp;n8n.<br>
    Audit, build, deploy, migrate, operate, hand over.<br>
    Every command, every gate, every failure mode.</div>
  <div class="meta">
    <b>Status</b> &nbsp;{m("Status", "Current")}<br>
    <b>Owner</b> &nbsp;{m("Owner")}<br>
    <b>Second operator</b> &nbsp;{m("Second operator", "Not yet named")}<br>
    <b>Source</b> &nbsp;docs/RUNBOOK.md — this PDF is generated from it<br>
    <b>Repository</b> &nbsp;{m("Repository")}<br>
    <b>Classification</b> &nbsp;{m("Classification", "Internal")}
  </div>
  <div class="warnstrip">
    This document is generated. Edit <b>docs/RUNBOOK.md</b> and re-run
    <b>docs/build_runbook_pdf.py</b> — never the PDF. An out-of-date runbook is
    worse than none, because it is trusted.
  </div>
</div>
"""


def build_toc(bookmarks, shift: int = 0) -> str:
    # WeasyPrint numbers bookmark pages from 0, and the contents pages are
    # inserted after the draft was measured — hence shift + 1, not shift.
    off = shift + 1
    rows = ['<div class="toc"><h1>Contents</h1>',
            '<div class="lede">Sections start on a new page. '
            'Reading paths by role are in §0.2; every gate is indexed in §20 B.</div>']
    for label, (page, *_), children, _state in bookmarks:
        rows.append(
            f'<div class="toc-row"><span>{html.escape(label)}</span>'
            f'<span>{page + off}</span></div>'
        )
        for sub, (spage, *_), _c, _s in children:
            rows.append(
                f'<div class="toc-row sub"><span>{html.escape(sub)}</span>'
                f'<span>{spage + off}</span></div>'
            )
    rows.append("</div>")
    return "\n".join(rows)


def postprocess(body: str) -> str:
    # Task-list checkboxes: the markdown library leaves these as literal text.
    body = re.sub(r"<li>\[ \]\s*", '<li class="task"><span class="box"></span>', body)
    body = re.sub(r"<li>\[x\]\s*", '<li class="task"><span class="box"></span>', body)
    # The first section heading follows the cover and TOC, which already broke.
    body = body.replace("<h2>", '<h2 class="nobreak">', 1)
    return body


def assemble(cover: str, toc: str, body: str) -> str:
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<title>Aspire CRM — Master Runbook</title>\n"
        f"<style>{CSS}</style>\n</head>\n<body>\n{cover}\n{toc}\n{body}\n</body>\n</html>"
    )


def main() -> int:
    try:
        from weasyprint import HTML
    except ImportError:
        sys.exit("WeasyPrint required:  pip install weasyprint")

    md, meta = prepare_source(SRC.read_text(encoding="utf-8"))
    body = postprocess(markdown_to_html(md))
    cover = build_cover(meta)

    # Pass 1 — no contents page, to discover where the headings actually land.
    draft = HTML(string=assemble(cover, "", body), base_url=str(HERE)).render()
    bookmarks = draft.make_bookmark_tree()

    # The contents page itself adds pages, shifting everything after it.
    toc_only = assemble(cover, build_toc(bookmarks), "")
    shift = len(HTML(string=toc_only, base_url=str(HERE)).render().pages) - 1

    doc = assemble(cover, build_toc(bookmarks, shift), body)
    OUT_PDF.parent.mkdir(exist_ok=True)
    final = HTML(string=doc, base_url=str(HERE)).render()
    final.write_pdf(OUT_PDF)

    words = len(re.sub(r"<[^>]+>", " ", body).split())
    print(f"Wrote {OUT_PDF.relative_to(ROOT)}")
    print(f"  {len(final.pages)} pages · {OUT_PDF.stat().st_size / 1024:.0f} KB "
          f"· ~{words:,} words")
    print(f"  contents occupies {shift} page(s); entries shifted to match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
