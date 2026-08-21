#!/usr/bin/env python3
"""
Assemble the build handbook and render it to PDF.

Three things in this file are deliberate and worth knowing before editing it.

**Terminal figures are real output, not typed.** Every console listing in the
handbook is read from docs/build-handbook/transcripts/, which holds the actual
captured output of the build. A handbook that paraphrases what a command
printed teaches the reader to accept output that does not match, which is the
opposite of what a build guide is for. The ANSI colours the scripts emit are
converted to spans so the page looks like the terminal did.

**Appendices are generated, not written.** The schema table, the workflow table
and the environment reference are built from reference/twenty_schema.yaml,
n8n/workflows/*.json and infra/.env.example at render time, so they cannot
drift from the files they describe.

**The contents page is measured, not typed.** The document is rendered once to
discover which page each heading landed on, then re-rendered with a contents
page built from that. Hand-typed page numbers are wrong the moment a paragraph
is edited.

Usage:
    python3 docs/build-handbook/build.py
    python3 docs/build-handbook/build.py --html-only    # skip the PDF render
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
IMG = HERE / "img"
TRANSCRIPTS = HERE / "transcripts"
OUT_PDF = ROOT / "out" / "Aspire-CRM-Build-Handbook.pdf"

PARTS = [f"part{i}.html" for i in range(9)]


# --------------------------------------------------------------------------
# Real terminal output → HTML
# --------------------------------------------------------------------------

# The scripts in this repo write colour with bare escape codes rather than
# through a library, so the set in use is small and closed.
ANSI = {
    "30": "k", "31": "r", "32": "g", "33": "y", "34": "b",
    "35": "m", "36": "c", "37": "w", "1": "bold", "2": "dim", "0": None,
}
_ESC = re.compile(r"\033\[([0-9;]*)m")


def ansi_to_html(text: str) -> str:
    """Convert captured terminal output to spans, preserving the colours."""
    out: list[str] = []
    open_spans = 0
    pos = 0
    for m in _ESC.finditer(text):
        out.append(html.escape(text[pos:m.start()], quote=False))
        pos = m.end()
        codes = [c for c in m.group(1).split(";") if c] or ["0"]
        if "0" in codes:
            out.append("</span>" * open_spans)
            open_spans = 0
            codes = [c for c in codes if c != "0"]
        classes = " ".join(f"a-{ANSI[c]}" for c in codes
                           if c in ANSI and ANSI[c])
        if classes:
            out.append(f'<span class="{classes}">')
            open_spans += 1
    out.append(html.escape(text[pos:], quote=False))
    out.append("</span>" * open_spans)
    return "".join(out)


def transcript(name: str, *, head: int = 0, tail: int = 0,
               grep: str = "") -> str:
    """
    Render a captured transcript as a terminal block.

    head/tail trim long output to the part being discussed. The trim is shown
    to the reader rather than hidden, because a listing that silently omits
    lines is one the reader cannot reproduce.
    """
    path = TRANSCRIPTS / name
    if not path.exists():
        return (f'<pre class="term missing">transcript {html.escape(name)} '
                f'not captured</pre>')
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if grep:
        lines = [ln for ln in lines if re.search(grep, ln)]
    trimmed = False
    if head and len(lines) > head:
        lines, trimmed = lines[:head], True
    elif tail and len(lines) > tail:
        lines, trimmed = lines[-tail:], True
    body = ansi_to_html("\n".join(lines))
    note = ('<div class="term-trim">output trimmed</div>') if trimmed else ""
    return f'<pre class="term">{body}</pre>{note}'


# --------------------------------------------------------------------------
# Figures
# --------------------------------------------------------------------------

def figure(name: str, caption: str, *, marks: list[tuple] | None = None,
           legend: list[str] | None = None, width: str = "100%") -> str:
    """
    A real screenshot with numbered callouts positioned over it.

    The PNG itself is never modified. Markers are absolutely positioned in
    CSS at percentage coordinates, so they stay crisp at any zoom, render
    identically in HTML and PDF, and can be moved later without image tools.

    marks: [(left%, top%, "1"), ...]
    """
    src = IMG / f"{name}.png"
    if not src.exists():
        return (f'<div class="fig missing">figure <code>{html.escape(name)}</code>'
                f" was not captured</div>")
    data = base64.b64encode(src.read_bytes()).decode()
    pins = "".join(
        f'<span class="pin" style="left:{x}%;top:{y}%">{html.escape(str(n))}</span>'
        for x, y, n in (marks or []))
    legend_html = ""
    if legend:
        items = "".join(
            f'<li><span class="pin-n">{i}</span>{txt}</li>'
            for i, txt in enumerate(legend, 1))
        legend_html = f'<ol class="fig-legend">{items}</ol>'
    return (
        f'<figure class="fig" style="max-width:{width}">'
        f'<div class="shot"><img src="data:image/png;base64,{data}" alt="">'
        f"{pins}</div>"
        f"<figcaption>{caption}</figcaption>{legend_html}</figure>")


# --------------------------------------------------------------------------
# Generated appendices
# --------------------------------------------------------------------------

def appendix_schema() -> str:
    import yaml
    schema = yaml.safe_load((ROOT / "reference" / "twenty_schema.yaml")
                            .read_text(encoding="utf-8"))
    objects = schema["objects"]
    rows = []
    for o in objects:
        fields = o.get("fields") or []
        rels = o.get("relations") or []
        rel_txt = ", ".join(f"→ {r['to']}" for r in rels) or "—"
        rows.append(
            f"<tr><td><code>{html.escape(o['nameSingular'])}</code></td>"
            f"<td>{html.escape(o['labelSingular'])}</td>"
            f"<td class='num'>{len(fields)}</td>"
            f"<td class='num'>{len(rels)}</td>"
            f"<td class='small'>{html.escape(rel_txt)}</td>"
            f"<td class='small'>{html.escape((o.get('description') or '')[:130])}</td></tr>")
    tot_f = sum(len(o.get("fields") or []) for o in objects)
    tot_r = sum(len(o.get("relations") or []) for o in objects)
    return (
        '<h2 class="newpage">Appendix C · The object model</h2>'
        f"<p>All <b>{len(objects)}</b> custom objects, <b>{tot_f}</b> scalar "
        f"fields and <b>{tot_r}</b> relations — <b>{tot_f + tot_r}</b> field "
        "definitions in total. Generated from "
        "<code>reference/twenty_schema.yaml</code>, so this table cannot "
        "disagree with what the provisioner builds.</p>"
        '<table class="wide"><tr><th>Object</th><th>Label</th>'
        '<th class="num">Fields</th><th class="num">Rel</th>'
        "<th>Relates to</th><th>Why it exists</th></tr>"
        + "".join(rows) + "</table>")


def appendix_workflows() -> str:
    wf_dir = ROOT / "n8n" / "workflows"
    dev = {"SYS Alert Sink (dev)", "SYS Failure Probe (dev)"}
    rows, total_nodes, total_exec = [], 0, 0
    for p in sorted(wf_dir.glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        nodes = d.get("nodes", [])
        sticky = [n for n in nodes if n["type"].endswith("stickyNote")]
        execn = len(nodes) - len(sticky)
        total_nodes += len(nodes)
        total_exec += execn
        triggers = sorted({
            n["type"].rsplit(".", 1)[-1] for n in nodes
            if n["type"].rsplit(".", 1)[-1].lower().endswith("trigger")
            or n["type"].endswith(".webhook")})
        kind = "dev only" if d["name"] in dev else "production"
        rows.append(
            f"<tr><td><b>{html.escape(d['name'])}</b></td>"
            f"<td class='small'>{html.escape(', '.join(triggers) or '—')}</td>"
            f"<td class='num'>{execn}</td>"
            f"<td class='num'>{len(sticky)}</td>"
            f"<td class='small'>{kind}</td></tr>")
    return (
        '<h2 class="newpage">Appendix D · The workflow library</h2>'
        f"<p><b>{len(rows)}</b> workflows — <b>{len(rows) - len(dev)}</b> "
        f"production and <b>{len(dev)}</b> that exist only to test the others. "
        f"<b>{total_exec}</b> executable nodes, plus <b>{total_nodes - total_exec}</b> "
        "sticky notes that document the canvas and never run. Generated from "
        "<code>n8n/workflows/*.json</code>.</p>"
        '<table class="wide"><tr><th>Workflow</th><th>Trigger</th>'
        '<th class="num">Steps</th><th class="num">Notes</th>'
        "<th>Deployed</th></tr>" + "".join(rows) + "</table>")


def appendix_env() -> str:
    """Every setting in infra/.env.example, with the comment that explains it."""
    text = (ROOT / "infra" / ".env.example").read_text(encoding="utf-8")
    rows, buf, section = [], [], ""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# ----"):
            section = s.strip("# -")
            rows.append(f'<tr class="sec"><td colspan="2">{html.escape(section)}</td></tr>')
            buf = []
        elif s.startswith("#"):
            buf.append(s.lstrip("# ").rstrip())
        elif "=" in s:
            key = s.split("=", 1)[0]
            note = " ".join(buf).strip()
            buf = []
            rows.append(
                f"<tr><td><code>{html.escape(key)}</code></td>"
                f"<td class='small'>{html.escape(note[:400]) or '—'}</td></tr>")
        elif not s:
            buf = []
    return (
        '<h2 class="newpage">Appendix B · Every setting in <code>infra/.env</code></h2>'
        "<p>Generated from <code>infra/.env.example</code> together with the "
        "comments that live beside each setting, so the explanation a builder "
        "reads here is the same one they read in the file.</p>"
        '<table class="wide"><tr><th style="width:31%">Setting</th>'
        "<th>What it does, and what breaks without it</th></tr>"
        + "".join(rows) + "</table>")


# --------------------------------------------------------------------------
# Assemble
# --------------------------------------------------------------------------

DIRECTIVE = re.compile(r"<!--\s*(FIGURE|TERM|APPENDIX)\s+(.*?)\s*-->", re.S)


def expand(markup: str) -> str:
    """Replace <!--FIGURE ...-->, <!--TERM ...--> and <!--APPENDIX ...-->."""
    def sub(m: re.Match) -> str:
        kind, payload = m.group(1), m.group(2)
        if kind == "APPENDIX":
            return {"schema": appendix_schema,
                    "workflows": appendix_workflows,
                    "env": appendix_env}[payload.strip()]()
        args = json.loads(payload)
        if kind == "FIGURE":
            return figure(**args)
        return transcript(**args)
    return DIRECTIVE.sub(sub, markup)


def build_toc(bookmarks, shift: int = 0) -> str:
    rows = ['<div class="toc">', "<h1>Contents</h1>"]
    for label, _dest, children, _state in bookmarks:
        rows.append(f'<div class="toc-part">{html.escape(label.strip())}</div>')
        for child, (page, _x, _y), _k, _s in children:
            rows.append('<div class="toc-row">'
                        f"<span>{html.escape(child.strip())}</span>"
                        f"<span>{page + shift + 1}</span></div>")
    rows.append("</div>")
    return "\n".join(rows)


def assemble(toc_html: str) -> str:
    shell = (HERE / PARTS[0]).read_text(encoding="utf-8")
    body = [expand(shell.partition("</body>")[0].replace("<!--TOC-->", toc_html))]
    for name in PARTS[1:]:
        p = HERE / name
        if p.exists():
            body.append(expand(p.read_text(encoding="utf-8")))
    body.append("</body></html>")
    return "\n".join(body)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--html-only", action="store_true")
    args = ap.parse_args()

    missing = [p for p in PARTS if not (HERE / p).exists()]
    if missing:
        print(f"note: {len(missing)} part(s) not written yet: {', '.join(missing)}")

    if args.html_only:
        (HERE / "_combined.html").write_text(assemble(""), encoding="utf-8")
        print("Wrote docs/build-handbook/_combined.html")
        return 0

    from weasyprint import HTML

    # Pass 1: render without a contents page to find where headings land.
    draft = HTML(string=assemble(""), base_url=str(HERE)).render()
    bookmarks = draft.make_bookmark_tree()

    # The contents page itself adds pages, shifting everything after it.
    head = assemble(build_toc(bookmarks)).split('<h1 class="part"')[0] + "</body></html>"
    shift = len(HTML(string=head, base_url=str(HERE)).render().pages) - 1

    doc = assemble(build_toc(bookmarks, shift))
    (HERE / "_combined.html").write_text(doc, encoding="utf-8")

    OUT_PDF.parent.mkdir(exist_ok=True)
    final = HTML(string=doc, base_url=str(HERE)).render()
    final.write_pdf(OUT_PDF)

    words = len(re.sub(r"<[^>]+>", " ", doc).split())
    figs = len(re.findall(r'<figure class="fig"', doc))
    terms = len(re.findall(r'<pre class="term"', doc))
    print(f"Wrote {OUT_PDF.relative_to(ROOT)}")
    print(f"  {len(final.pages)} pages · {OUT_PDF.stat().st_size/1024:.0f} KB "
          f"· ~{words:,} words")
    print(f"  {figs} screenshot(s) · {terms} terminal listing(s)")
    print(f"  contents occupies {shift} page(s); entries shifted to match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
