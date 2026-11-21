"""Write the browsable page, the CSV and the email body."""
from __future__ import annotations

import csv
import html
import json
from pathlib import Path

REGION_ORDER = ["Northern Indiana", "Southern Michigan", "Chicago Area", "Remote", "Location not listed"]


def _sort_key(m: dict):
    r = m["region"]
    return (REGION_ORDER.index(r) if r in REGION_ORDER else 99, not m["new"], m["company"].lower(), m["title"].lower())


def write_csv(path: Path, matches: list[dict]) -> None:
    cols = ["first_seen", "region", "company", "title", "location", "categories", "source", "posted", "url"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for m in sorted(matches, key=_sort_key):
            w.writerow({**m, "categories": ", ".join(m["categories"])})


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Internship Radar</title>
<style>
:root{--bg:#fafaf8;--fg:#1d1d1b;--mut:#6b6b66;--line:#e4e3de;--acc:#1f5fbf;--new:#0f7b3f}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecebe6;--mut:#9a9993;--line:#2c2c2a;--acc:#7fb0ff;--new:#4cc983}}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--fg)}
header{padding:20px 20px 8px;max-width:1200px;margin:auto}h1{margin:0 0 4px;font-size:22px}
.sub{color:var(--mut);font-size:13px}
.bar{display:flex;flex-wrap:wrap;gap:8px;padding:10px 20px;max-width:1200px;margin:auto;position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line)}
input,select{font:inherit;padding:6px 10px;border:1px solid var(--line);border-radius:6px;background:transparent;color:inherit}
#q{flex:1;min-width:180px}input[type=checkbox]{margin:0}label{display:flex;align-items:center;gap:4px;font-size:13px;color:var(--mut)}
main{max-width:1200px;margin:auto;padding:0 20px 40px}
table{width:100%;border-collapse:collapse;margin-top:8px}th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top}
td:first-child{white-space:nowrap}th{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);cursor:pointer;white-space:nowrap}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
.tag{display:inline-block;font-size:11px;padding:1px 6px;border:1px solid var(--line);border-radius:10px;margin:1px 2px 1px 0;color:var(--mut)}
.new{color:var(--new);font-weight:600;font-size:11px;margin-right:4px}.loc,.src{color:var(--mut);font-size:13px}
#count{color:var(--mut);font-size:13px;align-self:center}
@media (max-width:700px){td.src,th.src,td.cat,th.cat,td:first-child,th:first-child{display:none}main{padding:0 10px 40px}}
</style></head><body>
<header><h1>Internship Radar</h1>
<div class="sub">CS · IT · cyber · data · audit · business internships in Northern Indiana, Southern Michigan, Chicago and remote.
Updated __UPDATED__ · __NCOMP__ companies checked directly + community lists.</div></header>
<div class="bar">
<input id="q" placeholder="Search title, company, city…">
<select id="region"><option value="">All regions</option>__REGION_OPTS__</select>
<select id="cat"><option value="">All fields</option>__CAT_OPTS__</select>
<label><input type="checkbox" id="onlynew"> New this run</label>
<label><input type="checkbox" id="localonly"> Local companies only</label>
<span id="count"></span></div>
<main><table><thead><tr>
<th data-k="first_seen">Found</th><th data-k="company">Company</th><th data-k="title">Internship</th>
<th data-k="location">Location</th><th class="cat">Field</th><th class="src" data-k="source">Source</th>
</tr></thead><tbody id="rows"></tbody></table></main>
<script>
const DATA=__DATA__;
const $=id=>document.getElementById(id);let sortK="first_seen",asc=false;
function locs(s){const a=String(s||"").split("; ");return a.length>3?a.slice(0,3).join("; ")+` +${a.length-3} more`:a.join("; ")}
function esc(s){return String(s||"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
function render(){const q=$("q").value.toLowerCase(),r=$("region").value,c=$("cat").value,n=$("onlynew").checked,l=$("localonly").checked;
let rows=DATA.filter(m=>(!r||m.region===r)&&(!c||m.categories.includes(c))&&(!n||m.new)&&(!l||m.local)&&
(!q||(m.title+" "+m.company+" "+m.location).toLowerCase().includes(q)));
rows.sort((a,b)=>{const x=String(a[sortK]||""),y=String(b[sortK]||"");return (x<y?-1:x>y?1:0)*(asc?1:-1)});
$("count").textContent=rows.length+" of "+DATA.length;
$("rows").innerHTML=rows.map(m=>`<tr><td>${m.new?'<span class="new">NEW</span>':''}${esc(m.first_seen)}</td>
<td>${esc(m.company)}</td><td><a href="${esc(m.url)}" target="_blank" rel="noopener">${esc(m.title)}</a></td>
<td class="loc" title="${esc(m.location)}">${esc(locs(m.location)||m.region)}<br><span class="tag">${esc(m.region)}</span></td>
<td class="cat">${m.categories.map(t=>`<span class="tag">${esc(t)}</span>`).join("")}</td>
<td class="src">${esc(m.source)}</td></tr>`).join("")}
["q","region","cat","onlynew","localonly"].forEach(id=>$(id).addEventListener("input",render));
document.querySelectorAll("th[data-k]").forEach(th=>th.onclick=()=>{const k=th.dataset.k;asc=sortK===k?!asc:true;sortK=k;render()});
render();
</script></body></html>"""


def write_page(path: Path, matches: list[dict], updated: str, n_companies: int, categories: list[str]) -> None:
    regions = [r for r in REGION_ORDER if any(m["region"] == r for m in matches)]
    page = (PAGE.replace("__UPDATED__", html.escape(updated))
            .replace("__NCOMP__", str(n_companies))
            .replace("__REGION_OPTS__", "".join(f"<option>{html.escape(r)}</option>" for r in regions))
            .replace("__CAT_OPTS__", "".join(f"<option>{html.escape(c)}</option>" for c in categories + ["General"]))
            .replace("__DATA__", json.dumps(sorted(matches, key=_sort_key)).replace("</", "<\\/")))
    path.write_text(page, encoding="utf-8")


def _short_loc(loc: str) -> str:
    parts = [p for p in (loc or "").split("; ") if p]
    return "; ".join(parts[:3]) + (f" +{len(parts) - 3} more" if len(parts) > 3 else "")


def email_html(new: list[dict], total: int, page_url: str, limit: int = 150) -> str:
    rows, cur = [], None
    for m in sorted(new, key=_sort_key)[:limit]:
        if m["region"] != cur:
            cur = m["region"]
            rows.append(f'<tr><td colspan="3" style="padding:14px 0 4px;font-weight:600;font-size:15px;'
                        f'border-bottom:2px solid #ddd">{html.escape(cur)}</td></tr>')
        rows.append(
            '<tr><td style="padding:6px 8px 6px 0;border-bottom:1px solid #eee">'
            f'<a href="{html.escape(m["url"])}" style="color:#1f5fbf;text-decoration:none">{html.escape(m["title"])}</a>'
            f'<div style="color:#777;font-size:12px">{html.escape(", ".join(m["categories"]))}</div></td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #eee">{html.escape(m["company"])}</td>'
            f'<td style="padding:6px 0;border-bottom:1px solid #eee;color:#666;font-size:13px">'
            f'{html.escape(_short_loc(m["location"]) or m["region"])}</td></tr>')
    more = ""
    if len(new) > limit:
        more = f"<p>…and {len(new) - limit} more.</p>"
    link = f'<p><a href="{html.escape(page_url)}">See all {total} open matches →</a></p>' if page_url else ""
    return (f'<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:760px">'
            f'<h2 style="margin:0 0 4px">{len(new)} new internship{"s" if len(new) != 1 else ""}</h2>'
            f'<p style="color:#666;margin:0 0 8px">{total} open matches in total.</p>'
            f'<table style="border-collapse:collapse;width:100%;font-size:14px">{"".join(rows)}</table>'
            f'{more}{link}</div>')
