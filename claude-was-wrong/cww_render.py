"""Terminal, JSON compatibility, audit, and self-contained HTML renderers."""

from __future__ import annotations

import html
import json
import math
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

from cww_core import GROUP_LABELS, HEADLINE_PHRASES, snippet_for_row


BLOCKS = " ▁▂▃▄▅▆▇█"
HEAT = " ·░▒▓█"


def sparkline(values: list[float]) -> str:
    if not values:
        return "—"
    maximum = max(values)
    if maximum <= 0:
        return BLOCKS[0] * len(values)
    return "".join(BLOCKS[min(8, round(value / maximum * 8))] for value in values)


def bar(value: float, maximum: float, width: int = 24) -> str:
    filled = 0 if maximum <= 0 else round(value / maximum * width)
    return "█" * filled + "░" * (width - filled)


def table(headers: tuple[str, ...], rows: list[tuple[str, ...]], right: set[int] | None = None) -> str:
    right = right or set()
    widths = [len(value) for value in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    def line(row: tuple[str, ...]) -> str:
        return "  ".join(
            value.rjust(widths[index]) if index in right else value.ljust(widths[index])
            for index, value in enumerate(row)
        )

    return "\n".join(
        [line(headers), line(tuple("─" * width for width in widths))]
        + [line(row) for row in rows]
    )


def _section(title: str, width: int) -> str:
    suffix = "─" * max(1, width - len(title) - 3)
    return f"\n{title} {suffix}"


def render_tui(report: dict, *, color: bool = True) -> str:
    width = max(72, min(120, shutil.get_terminal_size((100, 24)).columns))
    summary = report["summary"]
    title = "CLAUDE CODE ERROR WEATHER"
    prominent = "YOU'RE / YOU WERE RIGHT AND I WAS WRONG"
    value = f'{summary["right_and_i_was_wrong"]:,}'
    population = report["filters"]["population"]
    population_note = (
        " (main and sub-agents shown separately)" if population == "all" else ""
    )
    inner = max(len(prominent), len(value), len(title)) + 4
    lines = [
        "╭" + "─" * inner + "╮",
        "│" + title.center(inner) + "│",
        "├" + "─" * inner + "┤",
        "│" + prominent.center(inner) + "│",
        "│" + value.center(inner) + "│",
        "╰" + "─" * inner + "╯",
        "",
        f'Window: {report["window"]["since"] or "all history"} through {report["window"]["until"]} ET',
        (
            f"Population: {population}{population_note}  "
            f'Classifier v{report["classifier_version"]}'
        ),
        "",
    ]
    cards = (
        ("Admitted", f'{summary["admissions"]:,}'),
        ("per 1k replies", f'{summary["admission_rate_per_1000"]:.2f}'),
        ("Apologies", f'{summary["apologies"]:,}'),
        ("Apology coverage", f'{summary["apology_coverage_pct"]:.1f}%'),
        ("Probable", f'{summary["probable"]:,}'),
    )
    lines.append("   ".join(f"{label}: {value}" for label, value in cards))

    lines.append(_section("Daily admission rate / 1,000 responses", width))
    populations = sorted({item["population"] for item in report["daily"]})
    for population in populations:
        items = [item for item in report["daily"] if item["population"] == population]
        values = [item["admission_rate_per_1000"] for item in items]
        suffix = f" max {max(values):.1f}" if values else ""
        lines.append(f"{population:9} {sparkline(values[-60:])}{suffix}")
    if report["roughest_day"]:
        item = report["roughest_day"]
        lines.append(
            f'Roughest sufficiently active day: {item["date"]} ({item["population"]}) — '
            f'{item["admission_rate_per_1000"]:.1f}/1k, n={item["responses"]:,}'
        )

    lines.append(_section("Admission types", width))
    exact_categories = [item for item in report["categories"] if item["tier"] == "exact"]
    maximum = max((item["responses"] for item in exact_categories), default=0)
    for item in exact_categories:
        lines.append(
            f'{item["label"][:30]:30} {bar(item["responses"], maximum)} '
            f'{item["responses"]:>5,}  {item["rate_per_1000"]:>6.2f}/1k'
        )

    lines.append(_section("Context fullness (estimated)", width))
    context_max = max(
        (item["admission_rate_per_1000"] for item in report["context"]), default=0
    )
    for population in populations:
        lines.append(f"{population}:")
        items = [item for item in report["context"] if item["population"] == population]
        for item in items:
            sparse = " sparse" if item["responses"] < 20 else ""
            lines.append(
                f'  {item["bucket"]:8} {bar(item["admission_rate_per_1000"], context_max, 18)} '
                f'{item["admission_rate_per_1000"]:>6.2f}/1k  n={item["responses"]:>5,}{sparse}'
            )

    lines.append(_section("Time of day (Eastern Time)", width))
    for population in populations:
        items = {item["hour"]: item for item in report["hourly"] if item["population"] == population}
        rates = [items.get(hour, {}).get("admission_rate_per_1000", 0) for hour in range(24)]
        maximum = max(rates, default=0)
        glyphs = "".join(
            HEAT[0] if items.get(hour, {}).get("responses", 0) < 20
            else HEAT[min(5, round((rates[hour] / maximum * 5) if maximum else 0))]
            for hour in range(24)
        )
        lines.append(f"{population:9} {glyphs}")
    lines.append("          00    06    12    18   23   (·/blank = sparse)")

    lines.append(_section("Top phrases", width))
    phrase_rows = [
        (
            item["phrase"],
            GROUP_LABELS[item["group"]],
            item["tier"],
            f'{item["occurrences"]:,}',
        )
        for item in report["phrases"][:15]
    ]
    lines.append(table(("Phrase", "Type", "Tier", "Uses"), phrase_rows, {3}))

    lines.append(_section("Coverage and caveats", width))
    lines.append(
        f'{summary["responses"]:,} deduplicated visible assistant responses; '
        f'context known for {summary["context_coverage_pct"]:.1f}%; '
        f'{summary["meta_mentions_excluded"]:,} meta/example mentions excluded.'
    )
    lines.append(
        "Admissions are an acknowledgment proxy, not a census of every mistake. "
        "Context and time relationships are descriptive, not causal."
    )
    return "\n".join(lines)


def legacy_json(report: dict, phrase_registry: list[str]) -> dict:
    phrase_counts = {item["phrase"]: item["occurrences"] for item in report["phrases"]}
    day_totals: dict[str, dict[str, int]] = {}
    for item in report["daily"]:
        aggregate = day_totals.setdefault(item["date"], {"admission_messages": 0, "sessions": 0})
        aggregate["admission_messages"] += item["admissions"]
    payload = dict(report)
    payload.update(
        {
            "assistant_messages_scanned": report["summary"]["responses"],
            "admission_messages": report["summary"]["admissions"],
            "right_and_i_was_wrong": report["summary"]["right_and_i_was_wrong"],
            "you_were_right_and_i_was_wrong": phrase_counts.get(HEADLINE_PHRASES[0], 0),
            "phrase_occurrences": {
                phrase: phrase_counts.get(phrase, 0) for phrase in phrase_registry
            },
            "legacy_daily": [
                {"date": day, **values} for day, values in sorted(day_totals.items())
            ],
        }
    )
    return payload


def audit_rows(report: dict, tier: str, include_snippets: bool, limit: int) -> list[dict]:
    output = []
    for row in sorted(report.get("rows", []), key=lambda item: item["timestamp_et"], reverse=True):
        selected = [
            match for match in row["matches"]
            if tier == "all" or match["tier"] == tier
        ]
        if not selected:
            continue
        item = {
            "timestamp_et": row["timestamp_et"],
            "population": row["population"],
            "model": row["model"],
            "project": row["project"],
            "session": str(row["session_id"])[:8],
            "context_pct": row["context_pct"],
            "matches": selected,
        }
        if include_snippets:
            item["snippet"] = snippet_for_row(row)
        output.append(item)
        if len(output) >= limit:
            break
    return output


def render_audit(rows: list[dict], include_snippets: bool) -> str:
    output = []
    for row in rows:
        context = f'{row["context_pct"]:.1f}%' if row["context_pct"] is not None else "?"
        labels = ", ".join(
            f'{match["phrase"]} [{match["tier"]} {match["score"]:.2f}]'
            for match in row["matches"]
        )
        output.append(
            f'{row["timestamp_et"]}  {row["population"]:8}  {context:>7}  '
            f'{row["project"]}  {row["session"]}\n  {labels}'
        )
        if include_snippets and row.get("snippet"):
            output.append(f'  “{row["snippet"]}”')
    return "\n".join(output) if output else "No matching responses."


def _safe_json(data: object) -> str:
    return (
        json.dumps(data, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def render_html(report: dict, output: Path, *, include_snippets: bool = False) -> None:
    rows = report.get("rows", [])
    client_rows = []
    for row in rows:
        item = {
            key: row[key]
            for key in (
                "timestamp_et", "date", "hour", "weekday", "population", "model",
                "project", "session_id", "context_pct", "has_admission",
                "has_apology", "has_probable", "combined", "matches",
            )
        }
        if include_snippets and (row["has_admission"] or row["has_probable"] or row["has_apology"]):
            item["snippet"] = snippet_for_row(row)
        client_rows.append(item)

    data = {
        "report": {key: value for key, value in report.items() if key != "rows"},
        "rows": client_rows,
    }
    title = "Claude Code Error Weather"
    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{title}</title>
<style>
:root{{--bg:#f7f8fa;--card:#fff;--ink:#18202b;--muted:#667085;--line:#d8dee8;--blue:#2f6fed;--gold:#c88a17;--orange:#d9612b;--pink:#b64175;--open:#e8eefb}}
@media(prefers-color-scheme:dark){{:root{{--bg:#10141b;--card:#171d27;--ink:#edf1f7;--muted:#aab4c4;--line:#303a49;--blue:#78a6ff;--gold:#e4b85c;--orange:#ef8d61;--pink:#df78aa;--open:#24324b}}}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,sans-serif}} main{{max-width:1120px;margin:auto;padding:38px 22px 80px}}
h1{{font-size:2.15rem;margin:0}} h2{{margin:42px 0 8px;font-size:1.35rem}} h3{{margin:0 0 6px;font-size:1rem}} p{{color:var(--muted);max-width:80ch}}
.eyebrow{{color:var(--blue);font-weight:700;letter-spacing:.08em;text-transform:uppercase;font-size:.75rem}}
.filters{{display:flex;gap:12px;flex-wrap:wrap;margin:24px 0}} label{{color:var(--muted);font-size:.82rem}} select{{display:block;margin-top:4px;padding:8px 28px 8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink)}}
.hero{{border:1px solid var(--line);background:var(--card);border-radius:18px;padding:24px;margin:22px 0}} .hero .big{{font:700 3.4rem/1 ui-monospace,monospace;color:var(--orange)}} .hero .label{{font-weight:700;margin-top:8px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:12px}} .card,.panel{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:17px}} .metric{{font:700 1.7rem ui-monospace,monospace}} .sub{{color:var(--muted);font-size:.82rem}}
.grid2{{display:grid;grid-template-columns:repeat(auto-fit,minmax(390px,1fr));gap:16px}} .panel{{margin-top:14px;overflow:hidden}} svg{{width:100%;height:auto;overflow:visible}} .axis{{stroke:var(--line);stroke-width:1}} .series-main{{stroke:var(--blue);fill:none;stroke-width:2.5}} .series-subagent{{stroke:var(--gold);fill:none;stroke-width:2.5;stroke-dasharray:5 4}}
.barrow{{display:grid;grid-template-columns:minmax(130px,1fr) 3fr 90px;gap:10px;align-items:center;margin:8px 0}} .track{{height:12px;background:var(--open);border-radius:8px;overflow:hidden}} .fill{{height:100%;background:var(--blue);border-radius:8px}} .num{{font-family:ui-monospace,monospace;text-align:right}}
.heat{{display:grid;grid-template-columns:repeat(24,minmax(8px,1fr));gap:2px}} .cell{{aspect-ratio:1;border-radius:2px;background:var(--open)}} .cloud{{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:baseline}} .cloud span{{color:var(--blue);font-weight:650}}
table{{width:100%;border-collapse:collapse;font-size:.86rem}} th,td{{padding:8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}} th{{color:var(--muted);font-weight:600}} code{{font-family:ui-monospace,monospace}} details{{margin-top:14px}} .warn{{border-left:4px solid var(--gold);padding-left:12px}} .sparse{{opacity:.35}}
@media(max-width:620px){{main{{padding:24px 14px}}.grid2{{grid-template-columns:1fr}}.barrow{{grid-template-columns:110px 1fr 70px}}}}
@media print{{.filters{{display:none}} body{{background:#fff}} .card,.panel,.hero{{break-inside:avoid}}}}
</style>
</head>
<body><main>
<div class="eyebrow">Admission proxy · Eastern Time</div>
<h1>{title}</h1>
<p id="scope"></p>
<div class="filters">
  <label>Population<select id="population"><option value="all">All, separated</option><option value="main">Main only</option><option value="subagent">Sub-agents only</option></select></label>
  <label>Model<select id="model"><option value="all">All models</option></select></label>
  <label>Project<select id="project"><option value="all">All projects</option></select></label>
</div>
<section class="hero"><div class="big" id="combined">0</div><div class="label">YOU'RE / YOU WERE RIGHT AND I WAS WRONG</div><div class="sub">High-confidence combined admissions</div></section>
<section class="cards" id="cards"></section>

<h2>The daily error weather</h2>
<p>Daily admitted-response rate per 1,000 visible replies. Lines separate main sessions from sub-agents; hover points for denominators.</p>
<div class="panel"><h3>Daily admission rate</h3><svg id="timeline" viewBox="0 0 900 300" role="img" aria-label="Daily admission rate line chart"></svg></div>

<div class="grid2">
<section><h2>What Claude admitted</h2><p>Response-level categories; a response may carry secondary tags, while the headline counts it once.</p><div class="panel" id="categories"></div></section>
<section><h2>When it happened</h2><p>Hourly admission rates in Eastern Time. Sparse hours with fewer than 20 responses are muted.</p><div class="panel"><div class="heat" id="hourly"></div><div class="sub">00 ET → 23 ET</div></div></section>
</div>

<h2>Does a fuller context correlate with admissions?</h2>
<p class="warn">These are descriptive rates using estimated model windows, not evidence that context fullness causes mistakes. Every bar includes its denominator and 95% Wilson interval.</p>
<div class="panel" id="context"></div>

<h2>Phrase weather</h2>
<p>The cloud is weighted by matched phrase frequency, not arbitrary words from private transcripts.</p>
<div class="panel"><div class="cloud" id="cloud"></div><details><summary>Exact phrase table</summary><table id="phrases"></table></details></div>

<h2>Coverage, definitions, and limitations</h2>
<div class="panel" id="methods"></div>
<details><summary>Auditable matches{(" with redacted snippets" if include_snippets else "")}</summary><div class="panel"><table id="audit"></table></div></details>
<noscript><p>This report needs JavaScript for interactive filters. The summary data is embedded locally and makes no network requests.</p></noscript>
</main>
<script id="payload" type="application/json">{_safe_json(data)}</script>
<script>
const DATA=JSON.parse(document.getElementById('payload').textContent);
const rows=DATA.rows, base=DATA.report;
const $=id=>document.getElementById(id);
const uniq=k=>[...new Set(rows.map(r=>r[k]).filter(Boolean))].sort();
for(const [id,key] of [['model','model'],['project','project']]) for(const value of uniq(key)){{const o=document.createElement('option');o.value=value;o.textContent=value;$(id).append(o)}}
const rate=(n,d)=>d?1000*n/d:0;
function filtered(){{return rows.filter(r=>($('population').value==='all'||r.population===$('population').value)&&($('model').value==='all'||r.model===$('model').value)&&($('project').value==='all'||r.project===$('project').value))}}
function group(a,key){{const m=new Map;for(const r of a){{const k=key(r);if(!m.has(k))m.set(k,[]);m.get(k).push(r)}}return m}}
function render(){{
 const a=filtered(), admitted=a.filter(r=>r.has_admission), apologies=a.filter(r=>r.has_apology), probable=a.filter(r=>r.has_probable);
 $('scope').textContent=`${{base.window.since||'All history'}} through ${{base.window.until}} ET · ${{a.length.toLocaleString()}} deduplicated visible assistant responses`;
 $('combined').textContent=a.filter(r=>r.combined).length.toLocaleString();
 const overlap=a.filter(r=>r.has_admission&&r.has_apology).length;
 const metrics=[['Admitted',admitted.length],['Admissions / 1k',rate(admitted.length,a.length).toFixed(2)],['Explicit apologies',apologies.length],['Apology coverage',admitted.length?(100*overlap/admitted.length).toFixed(1)+'%':'0%'],['Probable fuzzy',probable.length]];
 $('cards').innerHTML=metrics.map(x=>`<div class=card><div class=metric>${{x[1]}}</div><div class=sub>${{x[0]}}</div></div>`).join('');
 renderTimeline(a);renderCategories(a);renderHours(a);renderContext(a);renderPhrases(a);renderAudit(a);
 $('methods').innerHTML=`<p><b>Unit:</b> ${{base.methodology.unit}}. <b>Admission:</b> ${{base.methodology.admission_definition}}. <b>Probable:</b> ${{base.methodology.probable_definition}}.</p><p>${{base.methodology.caveat}} Context is known for ${{base.summary.context_coverage_pct}}% of the unfiltered report population; filters can change that coverage.</p>`;
}}
function renderTimeline(a){{
 const svg=$('timeline'), by=group(a,r=>r.date+'|'+r.population), dates=[...new Set(a.map(r=>r.date))].sort(), pops=['main','subagent'];let paths='',circles='';
 if(!dates.length){{svg.innerHTML='<text x=20 y=40>No data</text>';return}}
 const series={{}};let ymax=1;for(const p of pops){{series[p]=dates.map(d=>{{const x=by.get(d+'|'+p)||[],v=rate(x.filter(r=>r.has_admission).length,x.length);ymax=Math.max(ymax,v);return [v,x.length]}})}}
 const X=i=>50+(dates.length===1?0:i*(820/Math.max(1,dates.length-1))),Y=v=>255-v/ymax*210;
 for(const p of pops){{const points=series[p].map((v,i)=>`${{X(i)}},${{Y(v[0])}}`).join(' ');paths+=`<polyline class=series-${{p}} points="${{points}}"/>`;circles+=series[p].map((v,i)=>`<circle cx="${{X(i)}}" cy="${{Y(v[0])}}" r=3 fill="var(--${{p==='main'?'blue':'gold'}})"><title>${{dates[i]}} ${{p}}: ${{v[0].toFixed(2)}}/1k, n=${{v[1]}}</title></circle>`).join('')}}
 svg.innerHTML=`<line class=axis x1=50 y1=255 x2=870 y2=255/><line class=axis x1=50 y1=45 x2=50 y2=255/>${{paths}}${{circles}}<text x=50 y=285 fill="var(--muted)">${{dates[0]}}</text><text x=790 y=285 fill="var(--muted)">${{dates.at(-1)}}</text><text x=56 y=38 fill="var(--muted)">${{ymax.toFixed(1)}} /1k</text>`;
}}
function renderCategories(a){{
 const counts=new Map;for(const r of a)for(const m of r.matches.filter(m=>m.tier==='exact')){{if(m.group_name==='apology')continue;counts.set(m.group_name,(counts.get(m.group_name)||0)+1)}}const vals=[...counts].sort((x,y)=>y[1]-x[1]),max=Math.max(1,...vals.map(x=>x[1]));
 $('categories').innerHTML=vals.map(([k,v])=>`<div class=barrow><span>${{k}}</span><span class=track><span class=fill style="display:block;width:${{100*v/max}}%"></span></span><span class=num>${{v}} · ${{rate(v,a.length).toFixed(1)}}/1k</span></div>`).join('')||'<p>No admissions.</p>';
}}
function renderHours(a){{const by=group(a,r=>r.hour),vals=Array.from({{length:24}},(_,h)=>{{const x=by.get(h)||[];return [rate(x.filter(r=>r.has_admission).length,x.length),x.length]}}),max=Math.max(1,...vals.map(x=>x[0]));$('hourly').innerHTML=vals.map((v,h)=>`<div class="cell ${{v[1]<20?'sparse':''}}" style="background:color-mix(in srgb,var(--blue) ${{Math.max(8,100*v[0]/max)}}%,var(--open))"><title>${{String(h).padStart(2,'0')}}:00 ET · ${{v[0].toFixed(2)}}/1k · n=${{v[1]}}</title></div>`).join('')}}
function renderContext(a){{const labels=['0–10%','10–25%','25–50%','50–75%','75–90%','90%+'],bucket=v=>v<10?labels[0]:v<25?labels[1]:v<50?labels[2]:v<75?labels[3]:v<90?labels[4]:labels[5],known=a.filter(r=>r.context_pct!=null),by=group(known,r=>bucket(r.context_pct)),vals=labels.map(k=>{{const x=by.get(k)||[];return [k,rate(x.filter(r=>r.has_admission).length,x.length),x.length]}}),max=Math.max(1,...vals.map(x=>x[1]));$('context').innerHTML=vals.map(v=>`<div class="barrow ${{v[2]<20?'sparse':''}}"><span>${{v[0]}}</span><span class=track><span class=fill style="display:block;width:${{100*v[1]/max}}%"></span></span><span class=num>${{v[1].toFixed(1)}}/1k · n=${{v[2]}}</span></div>`).join('')}}
function renderPhrases(a){{const c=new Map;for(const r of a)for(const m of r.matches)c.set(m.phrase,(c.get(m.phrase)||0)+(m.occurrences||1));const vals=[...c].sort((x,y)=>y[1]-x[1]),max=Math.max(1,...vals.map(x=>x[1]));$('cloud').innerHTML=vals.slice(0,30).map(v=>`<span style="font-size:${{.82+1.5*Math.sqrt(v[1]/max)}}rem" title="${{v[1]}} occurrences">${{escapeHtml(v[0])}}</span>`).join('');$('phrases').innerHTML='<tr><th>Phrase</th><th>Occurrences</th></tr>'+vals.map(v=>`<tr><td>${{escapeHtml(v[0])}}</td><td>${{v[1]}}</td></tr>`).join('')}}
function renderAudit(a){{const hits=a.filter(r=>r.has_admission||r.has_apology||r.has_probable).sort((x,y)=>y.timestamp_et.localeCompare(x.timestamp_et)).slice(0,200);$('audit').innerHTML='<tr><th>Time (ET)</th><th>Scope</th><th>Matches</th>{("<th>Redacted snippet</th>" if include_snippets else "")}</tr>'+hits.map(r=>`<tr><td>${{r.timestamp_et}}</td><td>${{r.population}} · ${{escapeHtml(r.project)}}<br><code>${{String(r.session_id).slice(0,8)}}</code></td><td>${{r.matches.map(m=>escapeHtml(m.phrase)+' <small>'+m.tier+'</small>').join('<br>')}}</td>{("<td>${escapeHtml(r.snippet||'')}</td>" if include_snippets else "")}</tr>`).join('')}}
function escapeHtml(s){{return String(s).replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',\"'\":'&#39;'}}[c]))}}
for(const id of ['population','model','project'])$(id).addEventListener('change',render);render();
</script></body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
