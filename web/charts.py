"""
charts.py
=========
A small server-rendered SVG line chart. No JavaScript, no chart library, no CDN
-- which means the dashboard is guaranteed to work fully offline and there is no
third-party code in the page.

Interactivity comes from native SVG <title> elements, which browsers render as
tooltips on hover. That is enough for a clean financial dashboard.

The chart can only ever draw the points it is handed, and analytics.py bounds
those to the current simulated interval -- so the chart cannot leak future data.
"""

import html


def _nice_bounds(lo, hi):
    """Pad the value range a little so lines don't touch the frame."""
    if hi == lo:
        return lo * 0.95, hi * 1.05 if hi else 1.0
    pad = (hi - lo) * 0.08
    return lo - pad, hi + pad


def line_chart_svg(series, width=900, height=360, pad_left=72, pad_right=18,
                   pad_top=18, pad_bottom=46):
    """
    Render multiple series as an SVG line chart.

    `series` = [{"label": str, "color": str, "points": [{"date","value"}]}]
    Returns an SVG string ready to drop into a template.
    """
    series = [s for s in series if s.get("points")]
    if not series:
        return '<p class="muted">No data to plot yet.</p>'

    n = max(len(s["points"]) for s in series)
    all_values = [p["value"] for s in series for p in s["points"]]
    lo, hi = _nice_bounds(min(all_values), max(all_values))

    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom

    def x_of(i):
        return pad_left + (plot_w * i / (n - 1) if n > 1 else plot_w / 2)

    def y_of(v):
        return pad_top + plot_h - ((v - lo) / (hi - lo)) * plot_h

    parts = [
        f'<svg viewBox="0 0 {width} {height}" class="chart" '
        f'preserveAspectRatio="xMidYMid meet" role="img" '
        f'aria-label="Portfolio value versus benchmarks over time">'
    ]

    # --- horizontal gridlines + y-axis labels ---
    for t in range(5):
        v = lo + (hi - lo) * t / 4
        y = y_of(v)
        parts.append(
            f'<line x1="{pad_left}" y1="{y:.1f}" x2="{width - pad_right}" '
            f'y2="{y:.1f}" class="grid"/>')
        parts.append(
            f'<text x="{pad_left - 10}" y="{y + 4:.1f}" class="axis-label" '
            f'text-anchor="end">${v:,.0f}</text>')

    # --- x-axis labels: first, middle, last date (kept sparse and readable) ---
    ref = max(series, key=lambda s: len(s["points"]))["points"]
    for i in {0, (len(ref) - 1) // 2, len(ref) - 1}:
        if 0 <= i < len(ref):
            parts.append(
                f'<text x="{x_of(i):.1f}" y="{height - pad_bottom + 22:.1f}" '
                f'class="axis-label" text-anchor="middle">'
                f'{html.escape(ref[i]["date"])}</text>')

    # --- the series themselves ---
    for s in series:
        pts = s["points"]
        # Optional dash pattern lets series stay distinct within one colour
        # family (used by the navy/white theme: solid / dashed / dotted).
        dash = f' stroke-dasharray="{s["dash"]}"' if s.get("dash") else ""
        coords = " ".join(f"{x_of(i):.1f},{y_of(p['value']):.1f}"
                          for i, p in enumerate(pts))
        parts.append(
            f'<polyline points="{coords}" fill="none" '
            f'stroke="{s["color"]}" stroke-width="2.5"{dash} '
            f'stroke-linejoin="round" stroke-linecap="round"/>')

        # Final-value marker with a hover tooltip.
        last = pts[-1]
        parts.append(
            f'<circle cx="{x_of(len(pts) - 1):.1f}" '
            f'cy="{y_of(last["value"]):.1f}" r="4.5" fill="{s["color"]}">'
            f'<title>{html.escape(s["label"])}: ${last["value"]:,.2f} '
            f'on {html.escape(last["date"])}</title></circle>')

        # Invisible wide hover targets so every point has a tooltip.
        for i, p in enumerate(pts):
            parts.append(
                f'<circle cx="{x_of(i):.1f}" cy="{y_of(p["value"]):.1f}" '
                f'r="7" fill="transparent">'
                f'<title>{html.escape(s["label"])}: ${p["value"]:,.2f} '
                f'on {html.escape(p["date"])}</title></circle>')

    parts.append("</svg>")

    # --- legend ---
    # The swatch mirrors the line: a dashed series gets a dashed swatch, so the
    # legend is unambiguous even when the colours are close in the same family.
    def _swatch(s):
        if s.get("dash"):
            return (f'<i style="background:none;border-top:3px dashed '
                    f'{s["color"]}"></i>')
        return f'<i style="background:{s["color"]}"></i>'

    legend = "".join(
        f'<span class="legend-item">{_swatch(s)}'
        f'{html.escape(s["label"])}</span>' for s in series)
    parts.append(f'<div class="legend">{legend}</div>')

    return "".join(parts)
