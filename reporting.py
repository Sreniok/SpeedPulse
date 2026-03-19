#!/usr/bin/env python3
"""Shared report rendering utilities for SpeedPulse."""

from __future__ import annotations

import re
from datetime import datetime
from html import escape

DEFAULT_REPORT_THEME_ID = "default-dark"


def resolve_report_theme_id(config: dict, fallback: str = DEFAULT_REPORT_THEME_ID) -> str:
    notifications = config.get("notifications", {})
    raw_theme = str(notifications.get("report_theme_id", fallback) or "").strip().lower()
    if not raw_theme:
        return fallback
    if not re.fullmatch(r"[a-z0-9-]{3,64}", raw_theme):
        return fallback
    return raw_theme


def report_palette(theme_id: str) -> dict[str, str]:
    normalized = str(theme_id or DEFAULT_REPORT_THEME_ID).strip().lower()
    is_light = normalized.endswith("-light")

    if is_light:
        palette = {
            "bg": "#eef3fb",
            "surface": "#ffffff",
            "surface_alt": "#f8fbff",
            "text": "#142033",
            "muted": "#5a6b86",
            "accent": "#1f6feb",
            "accent_soft": "#dce9ff",
            "good": "#0f9f6e",
            "warn": "#c27902",
            "bad": "#d94157",
            "border": "#d4dfef",
            "shadow": "0 18px 40px rgba(15, 35, 64, 0.12)",
        }
    else:
        palette = {
            "bg": "#060c18",
            "surface": "#0f1e35",
            "surface_alt": "#142742",
            "text": "#e8f1ff",
            "muted": "#9eb4d8",
            "accent": "#66d3ff",
            "accent_soft": "#173e5e",
            "good": "#38d59f",
            "warn": "#f6b23b",
            "bad": "#ff6b83",
            "border": "#25496e",
            "shadow": "0 20px 52px rgba(2, 8, 22, 0.58)",
        }

    overrides = {
        "default-dark": {"accent": "#7fd7ff"},
        "cyber-matrix": {"accent": "#58a6ff", "good": "#2ea44f"},
        "carbon-amber": {"accent": "#f59e0b", "warn": "#fbbf24"},
        "noir-slate-dark": {"accent": "#a855f7", "bad": "#fb7185"},
        "github-dark": {"accent": "#58a6ff", "good": "#3fb950"},
        "atom-dark": {"accent": "#61afef", "good": "#98c379"},
        "monokai-dark": {"accent": "#f92672", "good": "#a6e22e"},
        "default-light": {"accent": "#1f6feb", "warn": "#d97706"},
        "paper-slate": {"accent": "#5b8def"},
        "linen-sage": {"accent": "#0f766e"},
        "soft-coral": {"accent": "#e76f51"},
        "github-light": {"accent": "#0969da", "good": "#1f883d"},
        "atom-light": {"accent": "#4078f2", "good": "#50a14f"},
        "monokai-light": {"accent": "#f92672", "good": "#66d9ef"},
    }
    palette.update(overrides.get(normalized, {}))
    return palette


def _avg(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 2)


def _fmt(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}"


def _as_number(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _metric_change(current: float, baseline: float, higher_is_better: bool = True) -> tuple[str, str]:
    if baseline <= 0:
        return "No baseline", "muted"
    delta = ((current - baseline) / baseline) * 100
    if abs(delta) < 0.1:
        return "Stable vs previous period", "muted"
    if higher_is_better:
        if delta > 0:
            return f"+{abs(delta):.1f}% vs previous period", "good"
        return f"-{abs(delta):.1f}% vs previous period", "bad"
    if delta < 0:
        return f"-{abs(delta):.1f}% vs previous period", "good"
    return f"+{abs(delta):.1f}% vs previous period", "bad"


def _entry_breach_flags(entry: dict, thresholds: dict) -> list[str]:
    breaches: list[str] = []
    if _as_number(entry.get("download_mbps")) < _as_number(thresholds.get("download_mbps")):
        breaches.append("Download")
    if _as_number(entry.get("upload_mbps")) < _as_number(thresholds.get("upload_mbps")):
        breaches.append("Upload")
    if _as_number(entry.get("ping_ms")) > _as_number(thresholds.get("ping_ms", 9_999_999)):
        breaches.append("Ping")
    if _as_number(entry.get("packet_loss_percent")) > _as_number(
        thresholds.get("packet_loss_percent", 9_999_999),
    ):
        breaches.append("Loss")
    return breaches


def _downsample_rows(rows: list[dict], max_points: int = 120) -> list[dict]:
    if len(rows) <= max_points:
        return list(rows)
    if max_points <= 1:
        return [rows[-1]]

    sampled: list[dict] = []
    last_index = len(rows) - 1
    for position in range(max_points):
        source_index = round((position * last_index) / (max_points - 1))
        item = rows[source_index]
        if not sampled or sampled[-1] is not item:
            sampled.append(item)
    return sampled


def _chart_timestamp_label(value: object, dense: bool = False) -> str:
    if isinstance(value, datetime):
        return value.strftime("%m-%d" if dense else "%m-%d %H:%M")
    return str(value or "")


def _chart_polyline(values: list[float], left: float, top: float, width: float, height: float, y_max: float) -> str:
    if not values:
        return ""

    safe_y_max = max(y_max, 1.0)
    if len(values) == 1:
        x = left + (width / 2)
        y = top + height - ((values[0] / safe_y_max) * height)
        return f"{x:.1f},{y:.1f}"

    points: list[str] = []
    for index, value in enumerate(values):
        x = left + (index * width / (len(values) - 1))
        y = top + height - ((value / safe_y_max) * height)
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def _build_chart_card(
    title: str,
    subtitle: str,
    rows: list[dict],
    *,
    palette: dict[str, str],
    series: list[dict[str, object]],
    y_unit: str,
) -> str:
    if not rows:
        return (
            "<section class=\"chart-card\">"
            f"<div class=\"chart-card-head\"><h3>{escape(title)}</h3><p>{escape(subtitle)}</p></div>"
            "<div class=\"chart-empty\">No test data available for this range.</div>"
            "</section>"
        )

    sampled_rows = _downsample_rows(rows)
    sampled_count = len(sampled_rows)
    dense_labels = sampled_count > 40
    left = 56.0
    top = 16.0
    width = 468.0
    height = 204.0
    baseline = 0.0

    plotted_series: list[dict[str, object]] = []
    max_value = 1.0
    for spec in series:
        key = str(spec.get("key") or "")
        values = [_as_number(item.get(key)) for item in sampled_rows]
        threshold_value = _as_number(spec.get("threshold"))
        max_value = max(max_value, max(values or [0.0]), threshold_value)
        plotted_series.append(
            {
                "label": str(spec.get("label") or key),
                "color": str(spec.get("color") or palette["accent"]),
                "values": values,
                "threshold": threshold_value,
                "threshold_label": str(spec.get("threshold_label") or "").strip(),
            }
        )

    y_max = max_value * 1.12
    if y_max <= 0:
        y_max = 1.0

    grid_lines: list[str] = []
    for step in range(5):
        ratio = step / 4
        y = top + (height * ratio)
        value = y_max * (1 - ratio)
        grid_lines.append(
            f"<line x1=\"{left:.1f}\" y1=\"{y:.1f}\" x2=\"{left + width:.1f}\" y2=\"{y:.1f}\" class=\"chart-grid-line\"></line>"
        )
        grid_lines.append(
            f"<text x=\"{left - 10:.1f}\" y=\"{y + 4:.1f}\" class=\"chart-axis-label chart-axis-label-y\">{escape(_fmt(value, 0))}{escape(y_unit)}</text>"
        )

    x_label_indexes = sorted({0, sampled_count // 2, sampled_count - 1})
    x_labels: list[str] = []
    for index in x_label_indexes:
        x = left + (width / 2 if sampled_count == 1 else index * width / (sampled_count - 1))
        anchor = "middle"
        if index == 0:
            anchor = "start"
        elif index == sampled_count - 1:
            anchor = "end"
        x_labels.append(
            f"<text x=\"{x:.1f}\" y=\"{top + height + 22:.1f}\" text-anchor=\"{anchor}\" class=\"chart-axis-label\">"
            f"{escape(_chart_timestamp_label(sampled_rows[index].get('timestamp'), dense_labels))}</text>"
        )

    threshold_lines: list[str] = []
    legend_items: list[str] = []
    series_lines: list[str] = []
    for spec in plotted_series:
        color = str(spec["color"])
        label = str(spec["label"])
        values = list(spec["values"])
        polyline = _chart_polyline(values, left, top, width, height, y_max)
        series_lines.append(
            f"<polyline points=\"{polyline}\" fill=\"none\" stroke=\"{color}\" stroke-width=\"3\" "
            "stroke-linecap=\"round\" stroke-linejoin=\"round\"></polyline>"
        )

        if values:
            last_x = left + (width / 2 if len(values) == 1 else width)
            last_y = top + height - ((values[-1] / y_max) * height)
            series_lines.append(
                f"<circle cx=\"{last_x:.1f}\" cy=\"{last_y:.1f}\" r=\"4.5\" fill=\"{color}\" stroke=\"{palette['surface']}\" stroke-width=\"2\"></circle>"
            )

        legend_items.append(
            "<span class=\"chart-legend-item\">"
            f"<span class=\"chart-legend-swatch\" style=\"background:{color}\"></span>"
            f"{escape(label)}"
            "</span>"
        )

        threshold_value = _as_number(spec.get("threshold"))
        threshold_label = str(spec.get("threshold_label") or "").strip()
        if threshold_value > baseline and threshold_label:
            threshold_y = top + height - ((threshold_value / y_max) * height)
            threshold_lines.append(
                f"<line x1=\"{left:.1f}\" y1=\"{threshold_y:.1f}\" x2=\"{left + width:.1f}\" y2=\"{threshold_y:.1f}\" "
                f"stroke=\"{color}\" stroke-width=\"1.5\" stroke-dasharray=\"6 5\" opacity=\"0.55\"></line>"
            )
            legend_items.append(
                "<span class=\"chart-legend-item chart-legend-item-threshold\">"
                f"<span class=\"chart-legend-swatch chart-legend-swatch-threshold\" style=\"color:{color}\"></span>"
                f"{escape(threshold_label)}"
                "</span>"
            )

    note = ""
    if len(rows) > sampled_count:
        note = f"Showing {sampled_count} sampled points from {len(rows)} tests."

    svg = (
        f"<svg class=\"report-chart\" viewBox=\"0 0 540 258\" role=\"img\" aria-label=\"{escape(title)}\">"
        f"<rect x=\"0.5\" y=\"0.5\" width=\"539\" height=\"257\" rx=\"16\" class=\"chart-frame\"></rect>"
        + "".join(grid_lines)
        + f"<line x1=\"{left:.1f}\" y1=\"{top + height:.1f}\" x2=\"{left + width:.1f}\" y2=\"{top + height:.1f}\" class=\"chart-axis\"></line>"
        + "".join(threshold_lines)
        + "".join(series_lines)
        + "".join(x_labels)
        + "</svg>"
    )

    return (
        "<section class=\"chart-card\">"
        f"<div class=\"chart-card-head\"><h3>{escape(title)}</h3><p>{escape(subtitle)}</p></div>"
        f"{svg}"
        f"<div class=\"chart-legend\">{''.join(legend_items)}</div>"
        f"<p class=\"chart-note\">{escape(note)}</p>"
        "</section>"
    )


def build_report_html(
    config: dict,
    entries: list[dict],
    *,
    report_title: str,
    range_label: str,
    generated_at: datetime | None = None,
    theme_id: str | None = None,
    previous_entries: list[dict] | None = None,
    rows_limit: int = 220,
) -> str:
    generated = generated_at or datetime.now()
    theme = str(theme_id or resolve_report_theme_id(config)).strip().lower()
    palette = report_palette(theme)
    thresholds = config.get("thresholds", {})
    account = config.get("account", {})

    rows = sorted(entries, key=lambda item: item.get("timestamp") or datetime.min)
    previous_rows = sorted(previous_entries or [], key=lambda item: item.get("timestamp") or datetime.min)

    download_values = [_as_number(item.get("download_mbps")) for item in rows]
    upload_values = [_as_number(item.get("upload_mbps")) for item in rows]
    ping_values = [_as_number(item.get("ping_ms")) for item in rows]
    jitter_values = [_as_number(item.get("jitter_ms")) for item in rows]
    loss_values = [_as_number(item.get("packet_loss_percent")) for item in rows]

    prev_download_avg = _avg([_as_number(item.get("download_mbps")) for item in previous_rows])
    prev_upload_avg = _avg([_as_number(item.get("upload_mbps")) for item in previous_rows])
    prev_ping_avg = _avg([_as_number(item.get("ping_ms")) for item in previous_rows])

    download_avg = _avg(download_values)
    upload_avg = _avg(upload_values)
    ping_avg = _avg(ping_values)
    jitter_avg = _avg(jitter_values)
    loss_avg = _avg(loss_values)

    breaches_download = sum(1 for item in rows if _as_number(item.get("download_mbps")) < _as_number(thresholds.get("download_mbps")))
    breaches_upload = sum(1 for item in rows if _as_number(item.get("upload_mbps")) < _as_number(thresholds.get("upload_mbps")))
    breaches_ping = sum(1 for item in rows if _as_number(item.get("ping_ms")) > _as_number(thresholds.get("ping_ms", 9_999_999)))
    breaches_loss = sum(
        1
        for item in rows
        if _as_number(item.get("packet_loss_percent")) > _as_number(thresholds.get("packet_loss_percent", 9_999_999))
    )

    compliance = 0.0
    if rows:
        healthy = sum(1 for item in rows if not _entry_breach_flags(item, thresholds))
        compliance = round((healthy / len(rows)) * 100, 1)

    dl_change_text, dl_change_tone = _metric_change(download_avg, prev_download_avg, True)
    ul_change_text, ul_change_tone = _metric_change(upload_avg, prev_upload_avg, True)
    ping_change_text, ping_change_tone = _metric_change(ping_avg, prev_ping_avg, False)

    throughput_chart = _build_chart_card(
        "Throughput trend",
        "Download and upload speeds across the selected report window.",
        rows,
        palette=palette,
        series=[
            {
                "key": "download_mbps",
                "label": "Download",
                "color": palette["accent"],
                "threshold": _as_number(thresholds.get("download_mbps")),
                "threshold_label": "Download floor",
            },
            {
                "key": "upload_mbps",
                "label": "Upload",
                "color": palette["good"],
                "threshold": _as_number(thresholds.get("upload_mbps")),
                "threshold_label": "Upload floor",
            },
        ],
        y_unit=" Mbps",
    )
    latency_chart = _build_chart_card(
        "Latency trend",
        "Ping and jitter over time for the same report window.",
        rows,
        palette=palette,
        series=[
            {
                "key": "ping_ms",
                "label": "Ping",
                "color": palette["bad"],
                "threshold": _as_number(thresholds.get("ping_ms")),
                "threshold_label": "Ping ceiling",
            },
            {
                "key": "jitter_ms",
                "label": "Jitter",
                "color": palette["warn"],
                "threshold": 0.0,
                "threshold_label": "",
            },
        ],
        y_unit=" ms",
    )

    newest_first = list(reversed(rows[-rows_limit:]))
    table_rows = ""
    for item in newest_first:
        timestamp = item.get("timestamp")
        if isinstance(timestamp, datetime):
            timestamp_label = timestamp.strftime("%Y-%m-%d %H:%M")
        else:
            timestamp_label = str(timestamp or "")
        server = escape(str(item.get("server", "Unknown")))
        source = escape(str(item.get("source", "scheduled")).capitalize())
        breaches = _entry_breach_flags(item, thresholds)
        status = "Healthy" if not breaches else f"Breach ({', '.join(breaches)})"
        tone_class = "ok" if not breaches else "bad"
        table_rows += (
            "<tr>"
            f"<td>{escape(timestamp_label)}</td>"
            f"<td>{source}</td>"
            f"<td>{server}</td>"
            f"<td>{_fmt(_as_number(item.get('download_mbps')))} Mbps</td>"
            f"<td>{_fmt(_as_number(item.get('upload_mbps')))} Mbps</td>"
            f"<td>{_fmt(_as_number(item.get('ping_ms')))} ms</td>"
            f"<td>{_fmt(_as_number(item.get('jitter_ms')))} ms</td>"
            f"<td>{_fmt(_as_number(item.get('packet_loss_percent')))}%</td>"
            f"<td class=\"{tone_class}\">{escape(status)}</td>"
            "</tr>"
        )

    if not table_rows:
        table_rows = (
            "<tr><td colspan=\"9\" class=\"muted\">No test data in this range yet.</td></tr>"
        )

    tests_displayed = min(len(rows), rows_limit)
    rows_note = ""
    if len(rows) > rows_limit:
        rows_note = (
            f"Showing the newest {tests_displayed} of {len(rows)} tests."
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{escape(report_title)}</title>
  <style>
    :root {{
      color-scheme: light dark;
    }}
    body {{
      margin: 0;
      padding: 24px;
      background: radial-gradient(circle at top right, {palette['surface_alt']} 0%, {palette['bg']} 42%);
      color: {palette['text']};
      font: 15px/1.45 "Segoe UI", "Avenir Next", "Trebuchet MS", sans-serif;
    }}
    .wrap {{
      max-width: 1120px;
      margin: 0 auto;
      background: linear-gradient(180deg, {palette['surface']} 0%, {palette['surface_alt']} 100%);
      border: 1px solid {palette['border']};
      border-radius: 20px;
      padding: 24px;
      box-shadow: {palette['shadow']};
    }}
    .eyebrow {{
      margin: 0;
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: {palette['muted']};
    }}
    h1 {{
      margin: 6px 0 8px;
      font-size: 34px;
      line-height: 1.1;
      letter-spacing: -0.02em;
    }}
    .meta {{
      margin: 0 0 18px;
      color: {palette['muted']};
      font-size: 13px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 14px;
    }}
    .card {{
      border: 1px solid {palette['border']};
      border-radius: 14px;
      padding: 12px;
      background: {palette['surface_alt']};
    }}
    .card h2 {{
      margin: 0;
      font-size: 12px;
      font-weight: 700;
      color: {palette['muted']};
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    .card p {{
      margin: 8px 0 0;
      font-size: 24px;
      font-weight: 700;
      letter-spacing: -0.02em;
    }}
    .trend {{
      margin-top: 5px;
      font-size: 12px;
      font-weight: 600;
    }}
    .good {{ color: {palette['good']}; }}
    .bad {{ color: {palette['bad']}; }}
    .muted {{ color: {palette['muted']}; }}
    .summary {{
      border: 1px solid {palette['border']};
      border-radius: 14px;
      padding: 14px;
      margin-bottom: 14px;
      background: {palette['surface_alt']};
    }}
    .chart-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 14px;
    }}
    .chart-card {{
      border: 1px solid {palette['border']};
      border-radius: 14px;
      padding: 12px;
      background: {palette['surface_alt']};
    }}
    .chart-card-head {{
      margin-bottom: 10px;
    }}
    .chart-card-head h3 {{
      margin: 0;
      font-size: 15px;
      letter-spacing: -0.01em;
    }}
    .chart-card-head p {{
      margin: 4px 0 0;
      color: {palette['muted']};
      font-size: 12px;
    }}
    .report-chart {{
      width: 100%;
      height: auto;
      display: block;
    }}
    .chart-frame {{
      fill: {palette['surface']};
      stroke: {palette['border']};
    }}
    .chart-grid-line {{
      stroke: {palette['border']};
      stroke-width: 1;
      opacity: 0.8;
    }}
    .chart-axis {{
      stroke: {palette['muted']};
      stroke-width: 1.2;
      opacity: 0.75;
    }}
    .chart-axis-label {{
      fill: {palette['muted']};
      font-size: 11px;
      font-family: "Segoe UI", "Avenir Next", "Trebuchet MS", sans-serif;
    }}
    .chart-axis-label-y {{
      text-anchor: end;
    }}
    .chart-legend {{
      margin-top: 10px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px 12px;
    }}
    .chart-legend-item {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: {palette['muted']};
      font-size: 12px;
      font-weight: 600;
    }}
    .chart-legend-swatch {{
      width: 10px;
      height: 10px;
      border-radius: 999px;
      display: inline-block;
      flex: 0 0 auto;
    }}
    .chart-legend-swatch-threshold {{
      width: 12px;
      height: 0;
      border-top: 2px dashed currentColor;
      border-radius: 0;
      background: transparent !important;
    }}
    .chart-note {{
      margin: 10px 0 0;
      color: {palette['muted']};
      font-size: 12px;
    }}
    .chart-note:empty {{
      display: none;
    }}
    .chart-empty {{
      min-height: 258px;
      border: 1px dashed {palette['border']};
      border-radius: 14px;
      display: grid;
      place-items: center;
      color: {palette['muted']};
      font-size: 13px;
      text-align: center;
      padding: 12px;
    }}
    .summary b {{
      color: {palette['accent']};
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      overflow: hidden;
      border-radius: 14px;
      border: 1px solid {palette['border']};
      font-size: 13px;
    }}
    th, td {{
      text-align: left;
      padding: 8px 10px;
      border-bottom: 1px solid {palette['border']};
      vertical-align: top;
    }}
    th {{
      background: {palette['accent_soft']};
      color: {palette['text']};
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    tr:nth-child(even) td {{
      background: {palette['surface_alt']};
    }}
    .foot {{
      margin-top: 12px;
      color: {palette['muted']};
      font-size: 12px;
      display: flex;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
    }}
    @media (max-width: 900px) {{
      body {{ padding: 10px; }}
      .wrap {{ padding: 14px; border-radius: 16px; }}
      .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .chart-grid {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 27px; }}
    }}
  </style>
</head>
<body>
  <article class="wrap">
    <p class="eyebrow">SpeedPulse report</p>
    <h1>{escape(report_title)}</h1>
    <p class="meta">
      {escape(range_label)} · Generated {escape(generated.strftime("%Y-%m-%d %H:%M"))} · Theme {escape(theme)}
    </p>

    <section class="grid">
      <div class="card">
        <h2>Total tests</h2>
        <p>{len(rows)}</p>
      </div>
      <div class="card">
        <h2>Download avg</h2>
        <p>{_fmt(download_avg)} Mbps</p>
        <div class="trend {dl_change_tone}">{escape(dl_change_text)}</div>
      </div>
      <div class="card">
        <h2>Upload avg</h2>
        <p>{_fmt(upload_avg)} Mbps</p>
        <div class="trend {ul_change_tone}">{escape(ul_change_text)}</div>
      </div>
      <div class="card">
        <h2>Ping avg</h2>
        <p>{_fmt(ping_avg)} ms</p>
        <div class="trend {ping_change_tone}">{escape(ping_change_text)}</div>
      </div>
    </section>

    <section class="summary">
      <div><b>Account:</b> {escape(str(account.get("name", "N/A")))} ({escape(str(account.get("number", "N/A")))})</div>
      <div><b>Compliance:</b> {_fmt(compliance, 1)}%</div>
      <div><b>Thresholds:</b> DL ≥ {_fmt(_as_number(thresholds.get("download_mbps")))} Mbps · UL ≥ {_fmt(_as_number(thresholds.get("upload_mbps")))} Mbps · Ping ≤ {_fmt(_as_number(thresholds.get("ping_ms")))} ms · Loss ≤ {_fmt(_as_number(thresholds.get("packet_loss_percent")))}%</div>
      <div><b>Breaches:</b> Download {breaches_download} · Upload {breaches_upload} · Ping {breaches_ping} · Loss {breaches_loss}</div>
      <div><b>Latency quality:</b> Jitter avg {_fmt(jitter_avg)} ms · Packet loss avg {_fmt(loss_avg)}%</div>
    </section>

    <section class="chart-grid">
      {throughput_chart}
      {latency_chart}
    </section>

    <table>
      <thead>
        <tr>
          <th>Timestamp</th>
          <th>Source</th>
          <th>Server</th>
          <th>Download</th>
          <th>Upload</th>
          <th>Ping</th>
          <th>Jitter</th>
          <th>Loss</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {table_rows}
      </tbody>
    </table>

    <div class="foot">
      <span>{escape(rows_note)}</span>
      <span>Generated by SpeedPulse</span>
    </div>
  </article>
</body>
</html>
"""
