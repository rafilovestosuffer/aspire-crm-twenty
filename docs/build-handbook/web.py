#!/usr/bin/env python3
"""
Render the handbook as a single self-contained web page.

Same source parts as the PDF, different shell. The print stylesheet is built
for A4 and paginated bookmarks; this one is built for a browser: a persistent
contents rail, a measure that stays readable at any window width, and both
colour themes.

Images are re-encoded to WebP on the way in. The PNGs are 8.5 MB, which is
fine in a repository and far too heavy for a page that has to inline
everything as data URIs.

Usage:
    python3 docs/build-handbook/web.py
    python3 docs/build-handbook/web.py --out /tmp/handbook.html
"""

from __future__ import annotations

import argparse
import base64
import io
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build as pdfbuild   # noqa: E402  — figures, transcripts, appendices

WEB_IMAGE_WIDTH = 1500
WEB_IMAGE_QUALITY = 78


def webp_figure(name: str, caption: str, *, marks=None, legend=None,
                width: str = "100%") -> str:
    """Same markup as the print figure, with a WebP payload."""
    from PIL import Image

    src = pdfbuild.IMG / f"{name}.png"
    if not src.exists():
        return (f'<div class="fig missing">figure <code>{name}</code> '
                f"was not captured</div>")

    im = Image.open(src).convert("RGB")
    if im.width > WEB_IMAGE_WIDTH:
        im = im.resize((WEB_IMAGE_WIDTH,
                        round(im.height * WEB_IMAGE_WIDTH / im.width)),
                       Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=WEB_IMAGE_QUALITY, method=6)
    data = base64.b64encode(buf.getvalue()).decode()

    pins = "".join(
        f'<span class="pin" style="left:{x}%;top:{y}%">{n}</span>'
        for x, y, n in (marks or []))
    legend_html = ""
    if legend:
        items = "".join(f'<li><span class="pin-n">{i}</span><span>{t}</span></li>'
                        for i, t in enumerate(legend, 1))
        legend_html = f'<ol class="fig-legend">{items}</ol>'
    return (f'<figure class="fig">'
            f'<div class="shot"><img loading="lazy" src="data:image/webp;base64,{data}" alt="">'
            f"{pins}</div>"
            f"<figcaption>{caption}</figcaption>{legend_html}</figure>")


def build_nav(html: str) -> str:
    """Contents rail, derived from the document's own headings."""
    rows, current = [], None
    pattern = re.compile(
        r'<h1 class="part"[^>]*><span class="num">(.*?)</span>(.*?)</h1>'
        r'|<h2[^>]*>(.*?)</h2>',
        re.S)
    for m in pattern.finditer(html):
        if m.group(1) is not None:
            if current is not None:
                rows.append("</ul></li>")
            label = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            num = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            current = label
            rows.append(f'<li class="nav-part"><span class="nav-num">{num}</span>'
                        f'<a href="#{slug(label)}">{label}</a><ul>')
        else:
            label = re.sub(r"<[^>]+>", "", m.group(3)).strip()
            rows.append(f'<li><a href="#{slug(label)}">{label}</a></li>')
    if current is not None:
        rows.append("</ul></li>")
    return "<ul class='nav-root'>" + "".join(rows) + "</ul>"


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "section"


def add_anchors(html: str) -> str:
    def h1(m):
        label = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        return (f'<h1 class="part" id="{slug(label)}">'
                f'<span class="num">{m.group(1)}</span>{m.group(2)}</h1>')

    def h2(m):
        label = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        cls = f' class="{m.group(1)}"' if m.group(1) else ""
        return f'<h2{cls} id="{slug(label)}">{m.group(2)}</h2>'

    html = re.sub(r'<h1 class="part"><span class="num">(.*?)</span>(.*?)</h1>',
                  h1, html, flags=re.S)
    html = re.sub(r'<h2(?: class="([^"]*)")?>(.*?)</h2>', h2, html, flags=re.S)
    return html


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "_web.html"))
    args = ap.parse_args()

    # Swap the figure renderer for the WebP one, then reuse the PDF assembler.
    pdfbuild.figure = webp_figure

    body = []
    for name in pdfbuild.PARTS:
        p = HERE / name
        if not p.exists():
            continue
        raw = p.read_text(encoding="utf-8")
        if name == "part0.html":
            # Keep only the body, and drop the print cover and contents
            # placeholder — the masthead and the rail replace both.
            raw = raw.partition("</head>")[2]
            raw = raw.partition('<div class="cover">')[0] + \
                raw.partition("<!--TOC-->")[2]
            raw = raw.replace("<body>", "")
        body.append(pdfbuild.expand(raw))
    html = "\n".join(body)
    html = add_anchors(html)
    # Tables carry the reference material and several are wide. Each gets its
    # own horizontal scroll container so the page body never scrolls sideways.
    html = re.sub(r"<table(.*?)</table>",
                  lambda m: f'<div class="tw"><table{m.group(1)}</table></div>',
                  html, flags=re.S)
    nav = build_nav(html)

    page = TEMPLATE.replace("<!--NAV-->", nav).replace("<!--BODY-->", html)
    out = Path(args.out)
    out.write_text(page, encoding="utf-8")
    kb = out.stat().st_size / 1024
    print(f"Wrote {out}  ({kb:.0f} KB)")
    if kb > 15000:
        print("  WARNING: approaching the 16 MB artifact ceiling")
    return 0


TEMPLATE = """<title>The Aspire CRM Build Handbook</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600;1,6..72,400&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
:root{
  --paper:#FBFCFD; --surface:#FFFFFF; --sunken:#F1F4F7;
  --ink:#131A22; --slate:#56656F; --rule:#DFE5EB; --rule-soft:#EAEFF3;
  --accent:#175E7A; --accent-dim:#E3EFF4; --accent-ink:#0F4257;
  --ok:#1C6B45; --ok-dim:#E4F1EA;
  --warn:#8A5D0C; --warn-dim:#F8F0DE;
  --danger:#A32E2E; --danger-dim:#F9EAEA;
  --term-bg:#0F1B26; --term-ink:#D3E0EA; --term-rule:#2A4054;
  --shadow:0 1px 2px rgba(19,26,34,.05), 0 10px 30px -18px rgba(19,26,34,.22);
  --display:"Newsreader",Georgia,"Times New Roman",serif;
  --body:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --paper:#0E141A; --surface:#151E26; --sunken:#1A242D;
    --ink:#E4EBF0; --slate:#93A3AE; --rule:#27343E; --rule-soft:#1E2831;
    --accent:#6FC0D8; --accent-dim:#12303C; --accent-ink:#9AD6E8;
    --ok:#66BE93; --ok-dim:#122B20;
    --warn:#D7A64F; --warn-dim:#2E2413;
    --danger:#E38585; --danger-dim:#331A1A;
    --term-bg:#0B141C; --term-rule:#263A4B;
    --shadow:0 1px 2px rgba(0,0,0,.35), 0 10px 30px -18px rgba(0,0,0,.7);
  }
}
:root[data-theme="dark"]{
  --paper:#0E141A; --surface:#151E26; --sunken:#1A242D;
  --ink:#E4EBF0; --slate:#93A3AE; --rule:#27343E; --rule-soft:#1E2831;
  --accent:#6FC0D8; --accent-dim:#12303C; --accent-ink:#9AD6E8;
  --ok:#66BE93; --ok-dim:#122B20;
  --warn:#D7A64F; --warn-dim:#2E2413;
  --danger:#E38585; --danger-dim:#331A1A;
  --term-bg:#0B141C; --term-rule:#263A4B;
  --shadow:0 1px 2px rgba(0,0,0,.35), 0 10px 30px -18px rgba(0,0,0,.7);
}

*{box-sizing:border-box}
body{
  margin:0; background:var(--paper); color:var(--ink);
  font-family:var(--body); font-size:16px; line-height:1.62;
  -webkit-font-smoothing:antialiased;
}

.masthead{
  border-bottom:1px solid var(--rule); background:var(--surface);
  padding:clamp(2.5rem,6vw,4.5rem) 1.5rem clamp(2rem,4vw,3rem);
}
.masthead .inner{max-width:1180px;margin:0 auto}
.eyebrow{
  font-family:var(--mono); font-size:.72rem; letter-spacing:.16em;
  text-transform:uppercase; color:var(--accent); margin-bottom:1.1rem;
}
.masthead h1{
  font-family:var(--display); font-weight:500; font-size:clamp(2.4rem,6.5vw,4rem);
  line-height:1.05; margin:0 0 1rem; letter-spacing:-.015em; text-wrap:balance;
}
.masthead .sub{
  font-size:clamp(1.02rem,2vw,1.2rem); color:var(--slate);
  max-width:62ch; margin:0 0 2rem; line-height:1.55;
}
.facts{
  display:grid; gap:1px; background:var(--rule);
  grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  border:1px solid var(--rule); border-radius:3px; overflow:hidden;
}
.facts div{background:var(--surface); padding:.95rem 1.05rem}
.facts b{
  display:block; font-family:var(--display); font-size:1.65rem;
  font-weight:600; line-height:1.1; color:var(--accent-ink);
  font-variant-numeric:tabular-nums;
}
.facts span{
  display:block; font-size:.76rem; color:var(--slate); margin-top:.2rem;
  letter-spacing:.02em;
}

.shell{max-width:1180px;margin:0 auto;padding:0 1.5rem;
  display:grid; grid-template-columns:255px minmax(0,1fr); gap:3.2rem}
@media (max-width:920px){.shell{grid-template-columns:1fr;gap:0}}

nav.rail{
  position:sticky; top:0; align-self:start; max-height:100vh; overflow-y:auto;
  padding:2.5rem 0 3rem; font-size:.83rem;
}
@media (max-width:920px){
  nav.rail{position:static;max-height:none;border-bottom:1px solid var(--rule);
    padding-bottom:1.5rem;margin-bottom:1rem}
}
nav.rail ul{list-style:none;margin:0;padding:0}
nav.rail .nav-root>li{margin-bottom:1.35rem}
nav.rail .nav-num{
  display:block; font-family:var(--mono); font-size:.66rem; letter-spacing:.14em;
  text-transform:uppercase; color:var(--accent); margin-bottom:.15rem;
}
nav.rail .nav-part>a{
  font-family:var(--display); font-size:1.02rem; font-weight:600;
  color:var(--ink); text-decoration:none; display:block; margin-bottom:.4rem;
}
nav.rail ul ul{border-left:1px solid var(--rule); padding-left:.85rem}
nav.rail ul ul a{
  display:block; color:var(--slate); text-decoration:none; padding:.16rem 0;
  line-height:1.4;
}
nav.rail a:hover{color:var(--accent)}
nav.rail a:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:2px}

main{padding:2.5rem 0 6rem; min-width:0}

h1.part{
  font-family:var(--display); font-weight:500; font-size:clamp(1.9rem,4vw,2.6rem);
  line-height:1.12; margin:4.5rem 0 1.4rem; letter-spacing:-.012em;
  padding-top:2.5rem; border-top:2px solid var(--ink); text-wrap:balance;
}
h1.part:first-child{margin-top:0;border-top:none;padding-top:0}
h1.part .num{
  display:block; font-family:var(--mono); font-size:.72rem; letter-spacing:.16em;
  text-transform:uppercase; color:var(--accent); margin-bottom:.55rem;
}
h2{
  font-family:var(--display); font-weight:600; font-size:1.42rem; line-height:1.25;
  margin:3.2rem 0 .9rem; padding-bottom:.45rem; border-bottom:1px solid var(--rule);
  scroll-margin-top:1rem; text-wrap:balance;
}
h3{font-family:var(--display);font-weight:600;font-size:1.14rem;margin:2.2rem 0 .6rem;color:var(--accent-ink)}
h4{font-size:.95rem;font-weight:600;margin:1.4rem 0 .45rem;letter-spacing:.005em}
p{margin:0 0 1rem;max-width:68ch}
ul,ol{margin:0 0 1rem;padding-left:1.25rem;max-width:68ch}
li{margin-bottom:.42rem}
b,strong{font-weight:600}
a{color:var(--accent)}
.lede{
  font-family:var(--display); font-size:1.2rem; line-height:1.5;
  color:var(--accent-ink); max-width:62ch; margin-bottom:1.4rem;
}

code{
  font-family:var(--mono); font-size:.86em; background:var(--sunken);
  padding:.1em .34em; border-radius:2px; color:var(--accent-ink);
  word-break:break-word;
}
pre{
  font-family:var(--mono); font-size:.79rem; line-height:1.55;
  background:var(--sunken); border-left:3px solid var(--accent);
  padding:.9rem 1.05rem; margin:1.1rem 0; overflow-x:auto; border-radius:0 3px 3px 0;
}
pre code{background:none;padding:0}
pre.cmd,pre.term{
  background:var(--term-bg); color:var(--term-ink);
  border-left:3px solid var(--accent); border-radius:0 3px 3px 0;
}
pre.cmd .p{color:#7ED9A5} pre.cmd .c{color:#8CA3B8}
pre.term.missing{background:var(--danger-dim);color:var(--danger)}
.a-g{color:#7ED9A5} .a-r{color:#F09292} .a-y{color:#E8C06A}
.a-c{color:#84D6E8} .a-b{color:#8FB6F2} .a-m{color:#D6A8EE}
.a-w{color:#E6EEF4} .a-k{color:#8CA3B8}
.a-bold{font-weight:500;color:#FFFFFF} .a-dim{color:#93A9BC}
.term-trim{font-size:.74rem;color:var(--slate);font-style:italic;margin:-.5rem 0 1.1rem .2rem}

.tw{overflow-x:auto;margin:1.1rem 0;border:1px solid var(--rule);border-radius:3px}
table{border-collapse:collapse;width:100%;font-size:.86rem;background:var(--surface)}
th{
  background:var(--sunken); text-align:left; padding:.62rem .8rem; font-weight:600;
  font-size:.78rem; letter-spacing:.02em; color:var(--accent-ink);
  border-bottom:1px solid var(--rule); white-space:nowrap;
}
td{padding:.58rem .8rem;border-bottom:1px solid var(--rule-soft);vertical-align:top}
tr:last-child td{border-bottom:none}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
tr.sec td{background:var(--accent-dim);font-weight:600;color:var(--accent-ink)}
.small{font-size:.92em;color:var(--slate)}
.kv td:first-child{font-weight:600;width:30%}

.callout{
  border-left:3px solid var(--accent); background:var(--accent-dim);
  padding:.95rem 1.1rem; margin:1.4rem 0; border-radius:0 3px 3px 0; max-width:70ch;
}
.callout.warn{border-left-color:var(--warn);background:var(--warn-dim)}
.callout.danger{border-left-color:var(--danger);background:var(--danger-dim)}
.callout.ok{border-left-color:var(--ok);background:var(--ok-dim)}
.callout .lbl{
  display:block; font-family:var(--mono); font-size:.68rem; font-weight:500;
  letter-spacing:.13em; text-transform:uppercase; color:var(--accent);
  margin-bottom:.42rem;
}
.callout.warn .lbl{color:var(--warn)}
.callout.danger .lbl{color:var(--danger)}
.callout.ok .lbl{color:var(--ok)}
.callout p:last-child,.callout ul:last-child{margin-bottom:0}

.step{
  border-left:3px solid var(--rule); padding-left:1.15rem; margin:1.8rem 0;
}
.step .n{
  display:inline-block; font-family:var(--mono); font-size:.66rem; font-weight:500;
  letter-spacing:.13em; background:var(--accent); color:var(--surface);
  padding:.16rem .5rem; border-radius:2px; margin-bottom:.6rem;
}
.step h4{margin-top:0}

figure.fig{margin:1.8rem 0 2rem}
figure.fig .shot{
  position:relative; border:1px solid var(--rule); border-radius:3px;
  overflow:hidden; box-shadow:var(--shadow); background:var(--surface);
}
figure.fig img{display:block;width:100%;height:auto}
figure.fig svg{display:block;width:100%;height:auto;background:var(--surface)}
figure.fig .pin{
  position:absolute; transform:translate(-50%,-50%);
  width:1.4rem; height:1.4rem; line-height:1.32rem; text-align:center;
  border-radius:50%; background:var(--danger); color:#fff;
  font-family:var(--mono); font-size:.7rem; font-weight:500;
  border:1.5px solid #fff; box-shadow:0 1px 4px rgba(0,0,0,.3);
}
figcaption{
  font-size:.83rem; color:var(--slate); line-height:1.5; margin-top:.7rem;
  padding-left:.8rem; border-left:2px solid var(--rule); max-width:70ch;
}
ol.fig-legend{list-style:none;padding:0;margin:.8rem 0 0;font-size:.83rem;max-width:70ch}
ol.fig-legend li{display:flex;gap:.55rem;margin-bottom:.4rem;color:var(--slate);line-height:1.45}
.pin-n{
  flex:0 0 auto; width:1.25rem; height:1.25rem; line-height:1.25rem; text-align:center;
  border-radius:50%; background:var(--danger); color:#fff;
  font-family:var(--mono); font-size:.66rem; margin-top:.1rem;
}
.fig.missing{border:1px dashed var(--danger);color:var(--danger);padding:1rem;font-size:.85rem;border-radius:3px}

footer{
  border-top:1px solid var(--rule); margin-top:4rem; padding:2.5rem 1.5rem 3.5rem;
  color:var(--slate); font-size:.85rem;
}
footer .inner{max-width:1180px;margin:0 auto}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style>

<header class="masthead">
  <div class="inner">
    <div class="eyebrow">Aspire Tech · Self-hosted CRM</div>
    <h1>The Build Handbook</h1>
    <p class="sub">From an empty machine to a working, proven CRM — every
    command, every screen, every trap. Written for a builder with no prior
    Docker or CRM experience, and for the engineer who follows them.</p>
    <div class="facts">
      <div><b>31</b><span>custom objects</span></div>
      <div><b>19</b><span>automations</span></div>
      <div><b>77</b><span>checks that prove it</span></div>
      <div><b>11</b><span>gated build steps</span></div>
      <div><b>16</b><span>screenshots, all real</span></div>
    </div>
  </div>
</header>

<div class="shell">
  <nav class="rail" aria-label="Contents"><!--NAV--></nav>
  <main><!--BODY--></main>
</div>

<footer><div class="inner">
  Every command in this handbook was run against a live build, and every
  screenshot is that build photographed. Counts are re-derived from the schema,
  the workflow library and the feature taxonomy by
  <code>docs/build-handbook/verify_facts.py</code>, which runs in CI — so the
  prose cannot drift from the source it describes.
</div></footer>
"""


if __name__ == "__main__":
    sys.exit(main())
