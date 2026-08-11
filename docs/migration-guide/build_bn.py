#!/usr/bin/env python3
"""
Assemble the Bangla companion guide and render it to PDF.

Requires Noto Sans Bengali. If it is missing the PDF renders as empty boxes,
so the build checks for it rather than producing a silently broken file.

Usage:
    python docs/migration-guide/build_bn.py
"""

from __future__ import annotations

import html
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
OUT_PDF = ROOT / "out" / "GHL-to-Twenty-Bangla-Explainer.pdf"
PARTS = ["bn1.html", "bn2.html", "bn3.html", "bn4.html"]


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def check_font() -> None:
    try:
        out = subprocess.run(["fc-list"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        print("WARNING: could not run fc-list; assuming the font is present.")
        return
    if "bengali" not in out.lower():
        sys.exit(
            "Noto Sans Bengali is not installed — the PDF would render as empty\n"
            "boxes. Install it first:\n"
            "  mkdir -p ~/.fonts && cd ~/.fonts\n"
            "  curl -sSLO <Noto Sans Bengali TTF from fonts.gstatic.com>\n"
            "  fc-cache -f")


def build_toc(bookmarks, shift: int = 0) -> str:
    rows = ['<div class="toc">', "<h1>সূচিপত্র</h1>"]
    for label, _dest, children, _state in bookmarks:
        rows.append(f'<div class="toc-part">{esc(label.strip())}</div>')
        for child_label, (page, _x, _y), _kids, _st in children:
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
    body.append("</body></html>")
    return "\n".join(body)


def main() -> int:
    try:
        from weasyprint import HTML
    except ImportError:
        sys.exit("WeasyPrint required:  pip install weasyprint")

    check_font()

    draft = HTML(string=assemble(""), base_url=str(HERE)).render()
    bookmarks = draft.make_bookmark_tree()

    cover_toc = (assemble(build_toc(bookmarks)).split("<h1>পর্ব ১")[0]
                 + "</body></html>")
    shift = len(HTML(string=cover_toc, base_url=str(HERE)).render().pages) - 1

    doc = assemble(build_toc(bookmarks, shift))
    (HERE / "_combined_bn.html").write_text(doc, encoding="utf-8")

    OUT_PDF.parent.mkdir(exist_ok=True)
    final = HTML(string=doc, base_url=str(HERE)).render()
    final.write_pdf(OUT_PDF)

    words = len(re.sub(r"<[^>]+>", " ", doc).split())
    print(f"Wrote {OUT_PDF.relative_to(ROOT)}")
    print(f"  {len(final.pages)} pages · {OUT_PDF.stat().st_size/1024:.0f} KB · ~{words:,} words")
    return 0


if __name__ == "__main__":
    sys.exit(main())

