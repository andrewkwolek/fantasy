"""Server-rendered inline SVG charts.

Everything is emitted as SVG with CSS custom properties for color, so charts
theme themselves in light and dark without a second code path and work with no
network access. Marks carry `data-tip` for the shared hover tooltip in app.js.

Palette rules enforced here (validated with the dataviz six-checks):
  * magnitude  -> one-hue sequential blue ramp
  * polarity   -> blue<->red diverging with a neutral gray midpoint
  * identity   -> at most 4 hues, and only in adjacent/stacked layouts; charts
                  with 10+ teams use one recessive ink plus a highlight instead,
                  because no categorical palette survives 10 simultaneous series.
"""
from __future__ import annotations

import html
import math
from typing import Sequence

# ---------------------------------------------------------------- primitives

# Heatmap fills stop short of the full pole so the value printed on top keeps
# >= 4.5:1 contrast in both themes (measured, not estimated).
CELL_INTENSITY = 0.70


def _svg_open(cls: str, width: int, height: int) -> str:
    """Open an SVG that scales down responsively but never above its design size.

    Without the max-width a 46px heatmap cell stretches to ~90px on a wide
    screen, which reads as a bug rather than a chart.
    """
    return (
        f'<svg class="{cls}" viewBox="0 0 {width} {height}" role="img" '
        f'preserveAspectRatio="xMidYMid meet" style="max-width:{width}px">'
    )


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def _fmt(value: float, digits: int = 1) -> str:
    return f"{value:,.{digits}f}"


def _nice_ticks(lo: float, hi: float, count: int = 5) -> list[float]:
    """Round, human-readable axis ticks spanning [lo, hi]."""
    if hi <= lo:
        return [lo]
    raw = (hi - lo) / max(1, count)
    mag = 10 ** math.floor(math.log10(raw))
    for mult in (1, 2, 2.5, 5, 10):
        step = mag * mult
        if raw <= step:
            break
    start = math.floor(lo / step) * step
    ticks, value = [], start
    while value <= hi + step * 0.5:
        if value >= lo - step * 0.001:
            ticks.append(round(value, 6))
        value += step
    return ticks


def _tick_digits(ticks: Sequence[float]) -> int:
    """Decimal places an axis needs so consecutive ticks read differently."""
    if len(ticks) < 2:
        return 1
    step = abs(ticks[1] - ticks[0])
    for threshold, digits in ((1, 0), (0.1, 1), (0.01, 2)):
        if step >= threshold:
            return digits
    return 3


def diverging_color(value: float, scale: float, *, max_intensity: float = 1.0) -> str:
    """Blue (positive) <-> gray (zero) <-> red (negative) as a CSS color-mix.

    color-mix keeps the ramp anchored to the themed pole tokens, so the same
    expression produces the light and dark ramps without a second table.
    """
    if scale <= 0:
        return "var(--div-mid)"
    t = max(-1.0, min(1.0, value / scale))
    pct = round(abs(t) * 100 * max_intensity)
    pole = "var(--div-pos)" if t >= 0 else "var(--div-neg)"
    return f"color-mix(in oklab, {pole} {pct}%, var(--div-mid))"


def sequential_color(value: float, lo: float, hi: float) -> str:
    """One-hue blue ramp, light (low) to dark (high)."""
    if hi <= lo:
        return "var(--seq-mid)"
    t = max(0.0, min(1.0, (value - lo) / (hi - lo)))
    return f"color-mix(in oklab, var(--seq-hi) {round(t * 100)}%, var(--seq-lo))"


# -------------------------------------------------------------------- charts

def bar_chart(
    rows: Sequence[dict],
    *,
    value_key: str = "value",
    label_key: str = "label",
    height_per_row: int = 30,
    width: int = 720,
    label_width: int = 168,
    digits: int = 1,
    suffix: str = "",
    color: str | None = None,
    diverging: bool = False,
    highlight: str | None = None,
) -> str:
    """Horizontal bars. `diverging` centres the axis on zero."""
    if not rows:
        return '<p class="empty">No data yet.</p>'

    values = [float(r[value_key]) for r in rows]
    pad_r = 78
    # A diverging chart draws bars to the left of zero, and their value labels
    # sit further left still -- without this gutter they land on the row labels.
    pad_l = 56 if diverging else 0
    plot_w = width - label_width - pad_l - pad_r
    height = len(rows) * height_per_row + 26
    bar_h = 15

    if diverging:
        span = max(abs(min(values)), abs(max(values))) or 1.0
        lo, hi = -span, span
    else:
        lo = min(0.0, min(values))
        hi = max(values) or 1.0

    def x_of(v: float) -> float:
        return label_width + pad_l + (v - lo) / (hi - lo) * plot_w

    parts = [_svg_open("chart", width, height)]

    ticks = _nice_ticks(lo, hi, 5)
    tick_d = _tick_digits(ticks)
    for tick in ticks:
        x = x_of(tick)
        parts.append(
            f'<line class="grid" x1="{x:.1f}" y1="16" x2="{x:.1f}" y2="{height - 12}"/>'
            f'<text class="tick" x="{x:.1f}" y="10" text-anchor="middle">'
            f'{_fmt(tick, tick_d)}</text>'
        )

    zero_x = x_of(0.0)
    parts.append(
        f'<line class="axis" x1="{zero_x:.1f}" y1="16" x2="{zero_x:.1f}" y2="{height - 12}"/>'
    )

    for i, row in enumerate(rows):
        value = float(row[value_key])
        y = 22 + i * height_per_row
        x = x_of(value)
        left, right = (zero_x, x) if value >= 0 else (x, zero_x)
        bar_w = max(1.5, right - left)
        fill = (
            diverging_color(value, max(abs(lo), abs(hi)))
            if diverging else (color or "var(--series-1)")
        )
        is_hi = highlight is not None and row.get(label_key) == highlight
        tip = row.get(
            "tip", f"{row.get(label_key,'')}: {_fmt(value, digits)}{suffix}"
        )
        # 4px rounded data-end anchored to the baseline.
        parts.append(
            f'<g class="mark{" is-hl" if is_hi else ""}" data-tip="{esc(tip)}">'
            f'<rect x="{left:.1f}" y="{y}" width="{bar_w:.1f}" height="{bar_h}" '
            f'rx="4" fill="{fill}"/>'
            f'<rect class="hit" x="{label_width}" y="{y - 6}" '
            f'width="{plot_w + pad_l + pad_r}" height="{height_per_row - 2}" '
            f'fill="transparent"/>'
            f'</g>'
        )
        anchor_x = (right + 6) if value >= 0 else (left - 6)
        align = "start" if value >= 0 else "end"
        parts.append(
            f'<text class="val" x="{anchor_x:.1f}" y="{y + 12}" text-anchor="{align}">'
            f'{_fmt(value, digits)}{esc(suffix)}</text>'
        )
        parts.append(
            f'<text class="lbl{" is-hl" if is_hi else ""}" x="{label_width - 10}" '
            f'y="{y + 12}" text-anchor="end">{esc(row.get(label_key, ""))}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


def grouped_bar(
    rows: Sequence[dict],
    series: Sequence[tuple[str, str]],
    *,
    label_key: str = "label",
    width: int = 720,
    row_height: int = 38,
    label_width: int = 168,
    digits: int = 1,
) -> str:
    """Two or three series side by side. Identity is legend + direct label."""
    if not rows or not series:
        return '<p class="empty">No data yet.</p>'
    pad_r = 72
    plot_w = width - label_width - pad_r
    height = len(rows) * row_height + 26
    n = len(series)
    bar_h = min(12, (row_height - 10) // n)

    hi = max(
        float(r[key]) for r in rows for key, _ in series
    ) or 1.0

    parts = [_svg_open("chart", width, height)]
    ticks = _nice_ticks(0, hi, 5)
    tick_d = _tick_digits(ticks)
    for tick in ticks:
        x = label_width + tick / hi * plot_w
        parts.append(
            f'<line class="grid" x1="{x:.1f}" y1="16" x2="{x:.1f}" y2="{height - 12}"/>'
            f'<text class="tick" x="{x:.1f}" y="10" text-anchor="middle">'
            f'{_fmt(tick, tick_d)}</text>'
        )

    for i, row in enumerate(rows):
        y0 = 20 + i * row_height
        parts.append(
            f'<text class="lbl" x="{label_width - 10}" y="{y0 + bar_h * n / 2 + 4}" '
            f'text-anchor="end">{esc(row.get(label_key,""))}</text>'
        )
        for s, (key, name) in enumerate(series):
            value = float(row[key])
            w = max(1.5, value / hi * plot_w)
            # 2px surface gap between adjacent fills.
            y = y0 + s * (bar_h + 2)
            parts.append(
                f'<g class="mark" data-tip="{esc(row.get(label_key,""))} — '
                f'{esc(name)}: {_fmt(value, digits)}">'
                f'<rect x="{label_width}" y="{y}" width="{w:.1f}" height="{bar_h}" '
                f'rx="4" fill="var(--series-{s + 1})"/></g>'
            )
        for s, (key, _name) in enumerate(series):
            value = float(row[key])
            parts.append(
                f'<text class="val" x="{label_width + value / hi * plot_w + 6:.1f}" '
                f'y="{y0 + s * (bar_h + 2) + bar_h - 2}">{_fmt(value, digits)}</text>'
            )
    parts.append("</svg>")

    legend = "".join(
        f'<span class="key"><i style="background:var(--series-{i + 1})"></i>{esc(name)}</span>'
        for i, (_, name) in enumerate(series)
    )
    return f'<div class="legend">{legend}</div>' + "".join(parts)


def heatmap(
    *,
    row_labels: Sequence[str],
    col_labels: Sequence[str],
    values: Sequence[Sequence[float | None]],
    center: float | None = None,
    digits: int = 0,
    cell_w: int = 62,
    cell_h: int = 32,
    label_width: int = 168,
    tips: Sequence[Sequence[str]] | None = None,
) -> str:
    """Team x week grid. Diverging around `center`, else sequential magnitude.

    Every cell carries its number, which satisfies the relief rule for the pale
    steps near the midpoint and makes the grid readable without color.
    """
    if not row_labels or not col_labels:
        return '<p class="empty">No data yet.</p>'

    flat = [v for row in values for v in row if v is not None]
    if not flat:
        return '<p class="empty">No data yet.</p>'
    lo, hi = min(flat), max(flat)
    scale = max(abs(hi - center), abs(center - lo)) if center is not None else 0.0

    width = label_width + len(col_labels) * cell_w + 8
    height = 24 + len(row_labels) * cell_h + 6

    parts = [_svg_open("chart heat", width, height)]
    for c, label in enumerate(col_labels):
        parts.append(
            f'<text class="tick" x="{label_width + c * cell_w + cell_w / 2:.1f}" '
            f'y="14" text-anchor="middle">{esc(label)}</text>'
        )
    for r, label in enumerate(row_labels):
        y = 24 + r * cell_h
        parts.append(
            f'<text class="lbl" x="{label_width - 10}" y="{y + cell_h / 2 + 4:.1f}" '
            f'text-anchor="end">{esc(label)}</text>'
        )
        for c, _ in enumerate(col_labels):
            value = values[r][c] if c < len(values[r]) else None
            x = label_width + c * cell_w
            if value is None:
                parts.append(
                    f'<rect class="cell empty-cell" x="{x + 1}" y="{y + 1}" '
                    f'width="{cell_w - 2}" height="{cell_h - 2}" rx="4"/>'
                )
                continue
            fill = (
                diverging_color(value - center, scale, max_intensity=CELL_INTENSITY)
                if center is not None else sequential_color(value, lo, hi)
            )
            tip = tips[r][c] if tips else f"{row_labels[r]} · {col_labels[c]}: {_fmt(value, digits)}"
            # 2px gap between fills; value printed in ink, never the series color.
            parts.append(
                f'<g class="mark" data-tip="{esc(tip)}">'
                f'<rect class="cell" x="{x + 1}" y="{y + 1}" width="{cell_w - 2}" '
                f'height="{cell_h - 2}" rx="4" fill="{fill}"/>'
                f'<text class="cellval" x="{x + cell_w / 2:.1f}" '
                f'y="{y + cell_h / 2 + 4:.1f}" text-anchor="middle">'
                f'{_fmt(value, digits)}</text></g>'
            )
    parts.append("</svg>")
    return "".join(parts)


def line_chart(
    series: Sequence[dict],
    *,
    x_labels: Sequence[str],
    width: int = 760,
    height: int = 300,
    invert: bool = False,
    y_label: str = "",
    digits: int = 1,
) -> str:
    """Many-series line chart.

    With 10+ teams no categorical palette can keep series apart, so every line
    is drawn in recessive ink and identity comes from hovering or clicking the
    legend, which promotes one line to the highlight color.
    """
    if not series or not x_labels:
        return '<p class="empty">No data yet.</p>'

    pad_l, pad_r, pad_t, pad_b = 44, 96, 14, 30
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    flat = [v for s in series for v in s["values"] if v is not None]
    if not flat:
        return '<p class="empty">No data yet.</p>'
    lo, hi = min(flat), max(flat)
    if invert:
        lo, hi = hi, lo
    if lo == hi:
        hi = lo + 1

    n = max(1, len(x_labels) - 1)

    def px(i: int) -> float:
        return pad_l + (i / n) * plot_w

    def py(v: float) -> float:
        return pad_t + (1 - (v - lo) / (hi - lo)) * plot_h

    parts = [_svg_open("chart lines", width, height)]
    y_ticks = _nice_ticks(min(lo, hi), max(lo, hi), 5)
    y_d = _tick_digits(y_ticks)
    for tick in y_ticks:
        y = py(tick)
        parts.append(
            f'<line class="grid" x1="{pad_l}" y1="{y:.1f}" x2="{pad_l + plot_w}" y2="{y:.1f}"/>'
            f'<text class="tick" x="{pad_l - 8}" y="{y + 4:.1f}" text-anchor="end">'
            f'{_fmt(tick, y_d)}</text>'
        )
    for i, label in enumerate(x_labels):
        if len(x_labels) > 12 and i % 2:
            continue
        parts.append(
            f'<text class="tick" x="{px(i):.1f}" y="{height - 10}" '
            f'text-anchor="middle">{esc(label)}</text>'
        )
    if y_label:
        parts.append(
            f'<text class="axis-label" x="{pad_l}" y="{pad_t - 2}">{esc(y_label)}</text>'
        )

    for s in series:
        points = [(px(i), py(v)) for i, v in enumerate(s["values"]) if v is not None]
        if len(points) < 1:
            continue
        d = " ".join(
            ("M" if i == 0 else "L") + f"{x:.1f} {y:.1f}" for i, (x, y) in enumerate(points)
        )
        key = esc(s.get("key", s["name"]))
        last_x, last_y = points[-1]
        parts.append(
            f'<g class="serie" data-serie="{key}">'
            f'<path class="line" d="{d}"/>'
            f'<circle class="dot" cx="{last_x:.1f}" cy="{last_y:.1f}" r="4"/>'
            f'<text class="serie-label" x="{last_x + 8:.1f}" y="{last_y + 4:.1f}">'
            f'{esc(s["name"])[:16]}</text></g>'
        )
    parts.append("</svg>")

    legend = "".join(
        f'<button class="key toggle" data-serie="{esc(s.get("key", s["name"]))}" '
        f'type="button">{esc(s["name"])}</button>'
        for s in series
    )
    return f'<div class="legend wrap">{legend}</div>' + "".join(parts)


def scatter(
    points: Sequence[dict],
    *,
    x_key: str = "x",
    y_key: str = "y",
    label_key: str = "label",
    width: int = 720,
    height: int = 400,
    x_title: str = "",
    y_title: str = "",
    diagonal: bool = False,
    color_key: str | None = None,
    color_scale: float = 1.0,
) -> str:
    """Single-series scatter; optional diverging color carries a second measure."""
    if not points:
        return '<p class="empty">No data yet.</p>'
    pad_l, pad_r, pad_t, pad_b = 56, 22, 18, 40
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    xs = [float(p[x_key]) for p in points]
    ys = [float(p[y_key]) for p in points]
    x_lo, x_hi = min(xs), max(xs)
    y_lo, y_hi = min(ys), max(ys)
    if diagonal:  # shared scale so the y=x reference line is meaningful
        x_lo = y_lo = min(x_lo, y_lo)
        x_hi = y_hi = max(x_hi, y_hi)
    xr = (x_hi - x_lo) or 1
    yr = (y_hi - y_lo) or 1
    x_lo, x_hi = x_lo - xr * 0.08, x_hi + xr * 0.08
    y_lo, y_hi = y_lo - yr * 0.08, y_hi + yr * 0.08

    def px(v): return pad_l + (v - x_lo) / (x_hi - x_lo) * plot_w
    def py(v): return pad_t + (1 - (v - y_lo) / (y_hi - y_lo)) * plot_h

    parts = [_svg_open("chart scatter", width, height)]
    for tick in _nice_ticks(y_lo, y_hi, 5):
        y = py(tick)
        parts.append(
            f'<line class="grid" x1="{pad_l}" y1="{y:.1f}" x2="{pad_l + plot_w}" y2="{y:.1f}"/>'
            f'<text class="tick" x="{pad_l - 8}" y="{y + 4:.1f}" text-anchor="end">'
            f'{_fmt(tick, 0)}</text>'
        )
    for tick in _nice_ticks(x_lo, x_hi, 5):
        x = px(tick)
        parts.append(
            f'<text class="tick" x="{x:.1f}" y="{height - 16}" text-anchor="middle">'
            f'{_fmt(tick, 0)}</text>'
        )
    if diagonal:
        parts.append(
            f'<line class="ref" x1="{px(max(x_lo, y_lo)):.1f}" y1="{py(max(x_lo, y_lo)):.1f}" '
            f'x2="{px(min(x_hi, y_hi)):.1f}" y2="{py(min(x_hi, y_hi)):.1f}"/>'
        )
    if x_title:
        parts.append(
            f'<text class="axis-label" x="{pad_l + plot_w / 2:.1f}" y="{height - 2}" '
            f'text-anchor="middle">{esc(x_title)}</text>'
        )
    if y_title:
        parts.append(
            f'<text class="axis-label" x="{pad_l - 40}" y="{pad_t + plot_h / 2:.1f}" '
            f'transform="rotate(-90 {pad_l - 40} {pad_t + plot_h / 2:.1f})" '
            f'text-anchor="middle">{esc(y_title)}</text>'
        )

    # Label placement. Every marker is registered as an obstacle first, then each
    # label takes the first candidate slot that hits neither another label nor a
    # dot. Without the marker obstacles a label happily lands on a neighbouring
    # point.
    obstacles: list[tuple[float, float, float, float]] = [
        (px(float(p[x_key])) - 8, py(float(p[y_key])) - 8,
         px(float(p[x_key])) + 8, py(float(p[y_key])) + 8)
        for p in points
    ]

    # (dx, dy, text-anchor) tried in order: above, below, then to either side.
    SLOTS = (
        (0, -12, "middle"), (0, 18, "middle"),
        (11, -9, "start"), (-11, -9, "end"),
        (11, 17, "start"), (-11, 17, "end"),
    )

    def _box(cx: float, cy: float, text: str, anchor: str):
        width_px = max(14.0, len(text) * 6.4)
        if anchor == "middle":
            x0, x1 = cx - width_px / 2, cx + width_px / 2
        elif anchor == "start":
            x0, x1 = cx, cx + width_px
        else:
            x0, x1 = cx - width_px, cx
        return (x0, cy - 8, x1, cy + 2)

    def free(box) -> bool:
        for other in obstacles:
            if not (box[2] < other[0] or box[0] > other[2]
                    or box[3] < other[1] or box[1] > other[3]):
                return False
        obstacles.append(box)
        return True

    # Two passes: every marker is drawn first, then every label. SVG paints in
    # document order, so interleaving them lets a later point's circle cover an
    # earlier point's text.
    labels: list[str] = []
    for p in points:
        x, y = px(float(p[x_key])), py(float(p[y_key]))
        fill = (
            diverging_color(float(p[color_key]), color_scale)
            if color_key else "var(--series-1)"
        )
        tip = p.get("tip", f"{p.get(label_key,'')}: {_fmt(float(p[x_key]))} / {_fmt(float(p[y_key]))}")
        # >=8px marker with a 2px surface ring so overlaps stay separable.
        parts.append(
            f'<g class="mark" data-tip="{esc(tip)}">'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="{fill}" '
            f'stroke="var(--surface-1)" stroke-width="2"/></g>'
        )
        text = str(p.get(label_key, ""))[:14]
        if text:
            for dx, dy, anchor in SLOTS:
                box = _box(x + dx, y + dy, text, anchor)
                if free(box):
                    labels.append(
                        f'<text class="pt-label" x="{x + dx:.1f}" y="{y + dy:.1f}" '
                        f'text-anchor="{anchor}">{esc(text)}</text>'
                    )
                    break
    parts.extend(labels)
    parts.append("</svg>")
    return "".join(parts)


def rows(
    items: Sequence[dict],
    *,
    label: str,
    value: str,
    tip: str | None = None,
    limit: int | None = None,
    scale: float = 1.0,
) -> list[dict]:
    """Adapt a list of analytics dicts into chart rows.

    Keeps templates free of data munging: charts take {label, value, tip}.
    """
    out = []
    for item in list(items)[:limit] if limit else items:
        row = {"label": item.get(label, ""), "value": item.get(value, 0) * scale}
        if tip and item.get(tip):
            row["tip"] = item[tip]
        out.append(row)
    return out


def dot_plot(
    rows: Sequence[dict],
    *,
    value_key: str = "value",
    label_key: str = "label",
    width: int = 560,
    height_per_row: int = 28,
    label_width: int = 168,
    digits: int = 1,
    suffix: str = "",
    reference: float | None = None,
    reference_label: str = "league average",
) -> str:
    """Horizontal dot plot for values clustered far from zero.

    A bar encodes magnitude by length and must therefore start at zero; when
    every value sits between 94% and 99% that produces ten identical bars. A dot
    encodes position, not length, so the axis can legitimately span just the
    range in play and the differences become visible.
    """
    if not rows:
        return '<p class="empty">No data yet.</p>'

    values = [float(r[value_key]) for r in rows]
    pad_r = 74
    plot_w = width - label_width - pad_r
    height = len(rows) * height_per_row + 30

    lo, hi = min(values), max(values)
    if reference is not None:
        lo, hi = min(lo, reference), max(hi, reference)
    span = (hi - lo) or 1.0
    lo, hi = lo - span * 0.12, hi + span * 0.12

    def x_of(v: float) -> float:
        return label_width + (v - lo) / (hi - lo) * plot_w

    parts = [_svg_open("chart dots", width, height)]
    ticks = _nice_ticks(lo, hi, 5)
    tick_d = _tick_digits(ticks)
    for tick in ticks:
        x = x_of(tick)
        parts.append(
            f'<line class="grid" x1="{x:.1f}" y1="18" x2="{x:.1f}" y2="{height - 12}"/>'
            f'<text class="tick" x="{x:.1f}" y="11" text-anchor="middle">'
            f'{_fmt(tick, tick_d)}</text>'
        )
    if reference is not None:
        rx = x_of(reference)
        parts.append(
            f'<line class="ref" x1="{rx:.1f}" y1="18" x2="{rx:.1f}" y2="{height - 12}"/>'
            f'<text class="tick" x="{rx:.1f}" y="{height - 2}" text-anchor="middle">'
            f'{esc(reference_label)}</text>'
        )

    baseline = x_of(lo)
    for i, row in enumerate(rows):
        value = float(row[value_key])
        y = 30 + i * height_per_row
        x = x_of(value)
        tip = row.get("tip", f"{row.get(label_key,'')}: {_fmt(value, digits)}{suffix}")
        parts.append(
            f'<g class="mark" data-tip="{esc(tip)}">'
            # Hairline stem keeps the row scannable without implying length.
            f'<line class="stem" x1="{baseline:.1f}" y1="{y:.1f}" x2="{x:.1f}" y2="{y:.1f}"/>'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5.5" fill="var(--series-1)" '
            f'stroke="var(--surface-1)" stroke-width="2"/>'
            f'<rect class="hit" x="{label_width}" y="{y - 12}" width="{plot_w + pad_r}" '
            f'height="{height_per_row}" fill="transparent"/></g>'
            f'<text class="val" x="{x + 11:.1f}" y="{y + 4:.1f}">'
            f'{_fmt(value, digits)}{esc(suffix)}</text>'
            f'<text class="lbl" x="{label_width - 10}" y="{y + 4:.1f}" text-anchor="end">'
            f'{esc(row.get(label_key, ""))}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)
