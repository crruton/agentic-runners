"""Сборка out/report.html (самодостаточный, офлайн) и out/metrics.json (детерминированный)."""

import json
import os
from html import escape

import pandas as pd

from runners import charts
from runners.metrics import KPI, compute

CSS = """
:root{--bg:#fbfaf7;--fg:#1d1c1a;--muted:#6b675f;--line:#e4e0d8;--card:#ffffff;--accent:#3b6ea8;--hi:#c2552d}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#17171a;--fg:#ecebe8;--muted:#a19d95;--line:#2d2d33;--card:#1f1f24;--accent:#7aa7dc;--hi:#e98a5f}}
:root[data-theme=dark]{--bg:#17171a;--fg:#ecebe8;--muted:#a19d95;--line:#2d2d33;--card:#1f1f24;--accent:#7aa7dc;--hi:#e98a5f}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1040px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:28px;margin:0 0 4px}h2{font-size:20px;margin:40px 0 4px}h3{font-size:15px;margin:20px 0 6px;color:var(--muted);font-weight:600}
.src{color:var(--muted);font-size:13px;margin:0 0 12px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:24px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.kpi b{display:block;font-size:26px;font-variant-numeric:tabular-nums}
.kpi span{color:var(--muted);font-size:13px}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:6px 20px;margin:8px 0 12px;padding:0;list-style:none}
.metrics li{border-bottom:1px solid var(--line);padding:4px 0;display:flex;justify-content:space-between;gap:12px}
.metrics li b{font-variant-numeric:tabular-nums;white-space:nowrap}
.chart{width:100%;height:auto;display:block;margin:8px 0}
.bar{fill:var(--accent)}.bar-hi{fill:var(--hi)}.grid{stroke:var(--line);stroke-width:1}
.lbl{fill:var(--muted);font-size:11px}.val{fill:var(--fg);font-size:11px}
.tbl{overflow-x:auto}
table{border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums;min-width:100%}
th,td{padding:4px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}th{color:var(--muted);font-weight:600}
.note{color:var(--muted);font-size:13px;margin:4px 0}
.missing{color:var(--hi)}
details{margin:8px 0}summary{cursor:pointer;color:var(--muted);font-size:13px}
"""


def fmt_value(m):
    v = m.value
    if v is None:
        return "—"
    if m.unit == "с" and v >= 120:
        return f"{v / 60:.1f} мин" if v < 7200 else f"{v / 3600:.1f} ч"
    if isinstance(v, int) or float(v).is_integer():
        s = f"{int(v):,}".replace(",", " ")
    else:
        s = f"{v:,.1f}".replace(",", " ")
    return f"{s}{m.unit}" if m.unit in ("×", "%") else f"{s} {m.unit}"


def table(df):
    df = df.astype(object)  # iterrows иначе приводит целые к float в строках со смешанными типами
    head = "".join(f"<th>{escape(str(c))}</th>" for c in [df.index.name or ""] + list(df.columns))
    rows = []
    for idx, r in df.iterrows():
        cells = [idx] + list(r.values)
        rows.append("<tr>" + "".join(f"<td>{escape(_cell(c))}</td>" for c in cells) + "</tr>")
    return f'<div class="tbl"><table><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


def _cell(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    if isinstance(v, pd.Timestamp):
        return f"{v:%Y-%m-%d}"
    if isinstance(v, float):
        return f"{v:.1f}"
    return str(v)


def _charts(sec):
    t = sec.tables
    out = []
    if sec.id == "trend":
        w = t["weeks"]
        lab = [f"{d:%d.%m}" for d in w.index]
        out += ["<h3>Заданий в неделю</h3>", charts.bars(lab, w.jobs, "заданий", label_every=1),
                "<h3>Часов раннеров в неделю</h3>", charts.bars(lab, w.runner_h, "ч", label_every=1)]
    elif sec.id == "queue":
        h = t["by_hour"]
        out += ["<h3>Очередь p95 по часу создания (МСК), мин</h3>",
                charts.bars(list(h.index), (h.queue_p95_s / 60).round(1), "мин", label_every=1)]
        d = t["by_day"]
        hi = {i for i, v in enumerate(d.queue_p95_s) if v and v > 1800}
        out += ["<h3>Очередь p95 по дням, мин (выделены дни больше 30 мин)</h3>",
                charts.bars([f"{x:%d.%m}" for x in d.index], (d.queue_p95_s / 60).round(1), "мин", hi)]
    elif sec.id == "canceled":
        d = t["by_day"]
        out += ["<h3>Отменено до старта по дням</h3>", charts.bars([f"{x:%d.%m}" for x in d.index], d.jobs, "заданий")]
    elif sec.id == "classes":
        c = t["classes"]
        out += ["<h3>Доля в занятом времени, %</h3>", charts.hbars(list(c.index), c.share_pct, "%", {0})]
    elif sec.id == "projects":
        p = t["projects"]
        out += ["<h3>Часы раннеров</h3>", charts.hbars(list(p.index), p.runner_h, "ч", {0})]
    elif sec.id == "hot":
        j = t["jobs"]
        out += ["<h3>Самые тяжёлые задания, часы</h3>", charts.hbars(list(j.index), j.busy_h, "ч")]
    elif sec.id == "whatif":
        qt = t["queue"]
        out += ["<h3>Очередь p95, мин: факт и модель</h3>",
                charts.hbars(list(qt.index), (qt.p95_s / 60).round(1), "мин", {2})]
    elif sec.id == "runners":
        r = t["runners"].dropna(subset=["busy_h"])
        lab = [f"#{i} · {row.cpu} · {row.ram}" for i, row in r.iterrows()]
        out += ["<h3>Занятость, часы</h3>", charts.hbars(lab, r.busy_h, "ч")]
    return "".join(out)


def section_html(sec):
    parts = [f'<section id="{sec.id}"><h2>{escape(sec.title)}</h2>',
             f'<p class="src">Источник: {escape(sec.source)} · период: {escape(sec.period or "—")}</p>']
    if sec.missing:
        parts.append('<p class="missing">Нет данных в снимке.</p></section>')
        return "".join(parts)
    if sec.metrics:
        parts.append('<ul class="metrics">' + "".join(
            f'<li><span>{escape(m.label)}</span><b title="{escape(m.source)} · {escape(m.period)}">{escape(fmt_value(m))}</b></li>'
            for m in sec.metrics) + "</ul>")
    parts.append(_charts(sec))
    for name, df in sec.tables.items():
        if len(df):
            parts.append(f"<details><summary>Таблица: {escape(name)} ({len(df)} строк)</summary>{table(df)}</details>")
    parts += [f'<p class="note">{escape(n)}</p>' for n in sec.notes]
    parts.append("</section>")
    return "".join(parts)


def metrics_json(snap, sections):
    ms = {m.id: {"label": m.label, "value": m.value, "unit": m.unit, "source": m.source, "period": m.period}
          for s in sections for m in s.metrics}
    doc = {"snapshot": os.path.basename(snap.path), "taken_at": snap.manifest.get("taken_at"), "metrics": ms}
    return json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def build(snap, out="out"):
    sections = compute(snap)
    by = {m.id: m for s in sections for m in s.metrics}
    kpis = "".join(f'<div class="kpi"><b>{escape(fmt_value(by[k]))}</b><span>{escape(by[k].label)}</span>'
                   f'<span> · {escape(by[k].source)}</span></div>' for k in KPI if k in by)
    snap_id = os.path.basename(snap.path)
    html = (f'<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Нагрузка на раннеры</title><style>{CSS}</style></head><body><main>'
            f'<h1>Нагрузка на CI-раннеры</h1>'
            f'<p class="src">Снимок {escape(snap_id)}, снят {escape(str(snap.manifest.get("taken_at")))}. '
            f'Все запуски CI в компании идут через агентов.</p>'
            f'<div class="kpis">{kpis}</div>'
            + "".join(section_html(s) for s in sections)
            + "</main></body></html>\n")
    os.makedirs(out, exist_ok=True)
    paths = [os.path.join(out, "report.html"), os.path.join(out, "metrics.json")]
    with open(paths[0], "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    with open(paths[1], "w", encoding="utf-8", newline="\n") as f:
        f.write(metrics_json(snap, sections))
    return paths
