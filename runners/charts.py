"""Минимальные SVG-графики для отчёта: вертикальные и горизонтальные столбцы.

Цвета задаются CSS-классами (.bar, .bar-hi, .grid, .lbl), темы переключаются переменными в report.py.
"""

from html import escape


def _fmt(v):
    if v is None:
        return "—"
    if abs(v) >= 1000:
        return f"{v:,.0f}".replace(",", " ")
    return f"{v:.0f}" if float(v).is_integer() or abs(v) >= 100 else f"{v:.1f}"


def _nice_max(v):
    if v <= 0:
        return 1
    import math
    e = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * e:
            return m * e
    return 10 * e


def bars(labels, values, unit="", highlight=None, width=720, height=240, label_every=None):
    """Вертикальные столбцы. highlight: множество индексов, выделяемых акцентом."""
    values = [0 if v is None else float(v) for v in values]
    n = len(values)
    if not n:
        return ""
    pad_l, pad_r, pad_t, pad_b = 52, 8, 26, 34
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    top = _nice_max(max(values))
    step = w / n
    bw = max(step * 0.72, 1)
    every = label_every or max(1, round(n / 12))
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" preserveAspectRatio="xMinYMin meet">']
    for k in range(5):
        y = pad_t + h - h * k / 4
        out.append(f'<line class="grid" x1="{pad_l}" x2="{width - pad_r}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text class="lbl" x="{pad_l - 6}" y="{y + 4:.1f}" text-anchor="end">{_fmt(top * k / 4)}</text>')
    hi = highlight or set()
    for i, (lab, v) in enumerate(zip(labels, values)):
        bh = h * v / top
        x = pad_l + i * step + (step - bw) / 2
        y = pad_t + h - bh
        cls = "bar-hi" if i in hi else "bar"
        out.append(f'<rect class="{cls}" x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="1.5">'
                   f'<title>{escape(str(lab))}: {_fmt(v)} {escape(unit)}</title></rect>')
        if i % every == 0:
            out.append(f'<text class="lbl" x="{x + bw / 2:.1f}" y="{height - pad_b + 16}" text-anchor="middle">{escape(str(lab))}</text>')
    if unit:
        out.append(f'<text class="lbl" x="{pad_l - 6}" y="12" text-anchor="end">{escape(unit)}</text>')
    out.append("</svg>")
    return "".join(out)


def hbars(labels, values, unit="", highlight=None, width=720, row=24):
    """Горизонтальные столбцы для рейтингов (проекты, классы, раннеры)."""
    values = [0 if v is None else float(v) for v in values]
    n = len(values)
    if not n:
        return ""
    pad_l, pad_r = 300, 70
    height = n * row + 8
    w = width - pad_l - pad_r
    top = max(values) or 1
    hi = highlight or set()
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" preserveAspectRatio="xMinYMin meet">']
    for i, (lab, v) in enumerate(zip(labels, values)):
        y = 4 + i * row
        bw = w * v / top
        text = str(lab) if len(str(lab)) <= 44 else str(lab)[:43] + "…"
        out.append(f'<text class="lbl" x="{pad_l - 8}" y="{y + row * 0.62:.1f}" text-anchor="end">{escape(text)}</text>')
        cls = "bar-hi" if i in hi else "bar"
        out.append(f'<rect class="{cls}" x="{pad_l}" y="{y + 3}" width="{max(bw, 1):.1f}" height="{row - 8}" rx="1.5">'
                   f'<title>{escape(str(lab))}: {_fmt(v)} {escape(unit)}</title></rect>')
        out.append(f'<text class="val" x="{pad_l + bw + 6:.1f}" y="{y + row * 0.62:.1f}">{_fmt(v)} {escape(unit)}</text>')
    out.append("</svg>")
    return "".join(out)
