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


def _rolling_average(values: list[float], window: int = 5) -> list[float]:
    if not values:
        return []
    safe_window = max(1, int(window))
    averaged: list[float] = []
    for index in range(len(values)):
        start = max(0, index - safe_window + 1)
        slice_values = values[start : index + 1]
        averaged.append(round(sum(slice_values) / len(slice_values), 2))
    return averaged


def _chart_area_path(values: list[float], left: float, top: float, width: float, height: float, y_max: float) -> str:
    if not values:
        return ""

    safe_y_max = max(y_max, 1.0)
    if len(values) == 1:
        x = left + (width / 2)
        y = top + height - ((values[0] / safe_y_max) * height)
        return (
            f"M {x:.1f} {top + height:.1f} "
            f"L {x:.1f} {y:.1f} "
            f"L {x:.1f} {top + height:.1f} Z"
        )

    commands = [f"M {left:.1f} {top + height:.1f}"]
    for index, value in enumerate(values):
        x = left + (index * width / (len(values) - 1))
        y = top + height - ((value / safe_y_max) * height)
        commands.append(f"L {x:.1f} {y:.1f}")
    commands.append(f"L {left + width:.1f} {top + height:.1f}")
    commands.append("Z")
    return " ".join(commands)


def _build_chart_card(
    eyebrow: str,
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
            "<section class=\"panel chart-panel\">"
            f"<div class=\"panel-head\"><p class=\"eyebrow\">{escape(eyebrow)}</p><h2>{escape(title)}</h2>"
            f"<p class=\"panel-subtext\">{escape(subtitle)}</p></div>"
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
    series_fills: list[str] = []
    for spec in plotted_series:
        color = str(spec["color"])
        label = str(spec["label"])
        values = list(spec["values"])
        polyline = _chart_polyline(values, left, top, width, height, y_max)
        series_fills.append(
            f"<path d=\"{_chart_area_path(values, left, top, width, height, y_max)}\" "
            f"fill=\"{color}\" opacity=\"0.08\"></path>"
        )
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

        trend_values = _rolling_average(values, 5)
        if len(values) >= 3:
            series_lines.append(
                f"<polyline points=\"{_chart_polyline(trend_values, left, top, width, height, y_max)}\" "
                f"fill=\"none\" stroke=\"{color}\" stroke-width=\"1.8\" stroke-dasharray=\"8 6\" "
                "stroke-linecap=\"round\" stroke-linejoin=\"round\" opacity=\"0.7\"></polyline>"
            )
            legend_items.append(
                "<span class=\"chart-legend-item\">"
                f"<span class=\"chart-legend-swatch chart-legend-swatch-trend\" style=\"color:{color}\"></span>"
                f"{escape(label)} trend"
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
        + "".join(series_fills)
        + "".join(series_lines)
        + "".join(x_labels)
        + "</svg>"
    )

    return (
        "<section class=\"panel chart-panel\">"
        f"<div class=\"panel-head\"><p class=\"eyebrow\">{escape(eyebrow)}</p><h2>{escape(title)}</h2>"
        f"<p class=\"panel-subtext\">{escape(subtitle)}</p></div>"
        f"<div class=\"chart-wrap\">{svg}</div>"
        f"<div class=\"chart-legend\">{''.join(legend_items)}</div>"
        f"<p class=\"chart-note\">{escape(note)}</p>"
        "</section>"
    )


def _build_breach_chart_card(
    rows: list[dict],
    *,
    palette: dict[str, str],
    items: list[dict[str, object]],
) -> str:
    if not rows:
        return (
            "<section class=\"panel chart-panel panel-wide\">"
            "<div class=\"panel-head\"><p class=\"eyebrow\">Reliability</p><h2>Threshold breaches</h2>"
            "<p class=\"panel-subtext\">Counts of tests outside your configured minimum and maximum values.</p></div>"
            "<div class=\"chart-empty\">No test data available for this range.</div>"
            "</section>"
        )

    left = 54.0
    top = 18.0
    width = 458.0
    height = 172.0
    total = max(1, len(items))
    max_value = max((_as_number(item.get("value")) for item in items), default=0.0)
    y_max = max(1.0, max_value * 1.25)

    grid_lines: list[str] = []
    for step in range(5):
        ratio = step / 4
        y = top + (height * ratio)
        value = y_max * (1 - ratio)
        grid_lines.append(
            f"<line x1=\"{left:.1f}\" y1=\"{y:.1f}\" x2=\"{left + width:.1f}\" y2=\"{y:.1f}\" class=\"chart-grid-line\"></line>"
        )
        grid_lines.append(
            f"<text x=\"{left - 10:.1f}\" y=\"{y + 4:.1f}\" class=\"chart-axis-label chart-axis-label-y\">{escape(_fmt(value, 0))}</text>"
        )

    step_width = width / total
    bar_width = min(76.0, step_width * 0.58)
    bars: list[str] = []
    x_labels: list[str] = []
    legend_items: list[str] = []
    total_breaches = 0
    for index, item in enumerate(items):
        value = _as_number(item.get("value"))
        color = str(item.get("color") or palette["accent"])
        label = str(item.get("label") or "")
        short_label = str(item.get("short_label") or label)
        total_breaches += int(value)
        x = left + (index * step_width) + ((step_width - bar_width) / 2)
        bar_height = (value / y_max) * height if y_max > 0 else 0.0
        y = top + height - bar_height
        bars.append(
            f"<rect x=\"{x:.1f}\" y=\"{y:.1f}\" width=\"{bar_width:.1f}\" height=\"{max(bar_height, 1.5):.1f}\" "
            f"rx=\"12\" fill=\"{color}\" opacity=\"0.3\" stroke=\"{color}\" stroke-width=\"1.4\"></rect>"
        )
        bars.append(
            f"<text x=\"{x + (bar_width / 2):.1f}\" y=\"{max(top + 12, y - 8):.1f}\" text-anchor=\"middle\" class=\"chart-axis-label chart-value-label\">{int(value)}</text>"
        )
        x_labels.append(
            f"<text x=\"{x + (bar_width / 2):.1f}\" y=\"{top + height + 24:.1f}\" text-anchor=\"middle\" class=\"chart-axis-label\">{escape(short_label)}</text>"
        )
        legend_items.append(
            "<span class=\"chart-legend-item\">"
            f"<span class=\"chart-legend-swatch\" style=\"background:{color}\"></span>"
            f"{escape(label)}"
            "</span>"
        )

    note = (
        f"{total_breaches} total breach event{'s' if total_breaches != 1 else ''} across this report window."
        if total_breaches
        else "No threshold breaches recorded in this range."
    )

    svg = (
        "<svg class=\"report-chart\" viewBox=\"0 0 540 258\" role=\"img\" aria-label=\"Threshold breaches\">"
        "<rect x=\"0.5\" y=\"0.5\" width=\"539\" height=\"257\" rx=\"16\" class=\"chart-frame\"></rect>"
        + "".join(grid_lines)
        + f"<line x1=\"{left:.1f}\" y1=\"{top + height:.1f}\" x2=\"{left + width:.1f}\" y2=\"{top + height:.1f}\" class=\"chart-axis\"></line>"
        + "".join(bars)
        + "".join(x_labels)
        + "</svg>"
    )

    return (
        "<section class=\"panel chart-panel panel-wide\">"
        "<div class=\"panel-head\"><p class=\"eyebrow\">Reliability</p><h2>Threshold breaches</h2>"
        "<p class=\"panel-subtext\">Counts of tests outside your configured minimum and maximum values.</p></div>"
        f"<div class=\"chart-wrap\">{svg}</div>"
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
        "Speed",
        "Download / Upload",
        "Download and upload speeds across the selected report window, with rolling trend lines and threshold markers.",
        rows,
        palette=palette,
        series=[
            {
                "key": "download_mbps",
                "label": "Download",
                "color": palette["good"],
                "threshold": _as_number(thresholds.get("download_mbps")),
                "threshold_label": "Download floor",
            },
            {
                "key": "upload_mbps",
                "label": "Upload",
                "color": palette["accent"],
                "threshold": _as_number(thresholds.get("upload_mbps")),
                "threshold_label": "Upload floor",
            },
        ],
        y_unit=" Mbps",
    )
    latency_chart = _build_chart_card(
        "Latency",
        "Ping / Jitter",
        "Ping and jitter readings across the same report window, with the configured ping ceiling overlaid.",
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
    breach_chart = _build_breach_chart_card(
        rows,
        palette=palette,
        items=[
            {
                "label": "Download below minimum",
                "short_label": "Download",
                "value": breaches_download,
                "color": palette["good"],
            },
            {
                "label": "Upload below minimum",
                "short_label": "Upload",
                "value": breaches_upload,
                "color": palette["accent"],
            },
            {
                "label": "Ping above maximum",
                "short_label": "Ping",
                "value": breaches_ping,
                "color": palette["bad"],
            },
            {
                "label": "Loss above maximum",
                "short_label": "Loss",
                "value": breaches_loss,
                "color": palette["warn"],
            },
        ],
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
    rows_note = f"Showing the newest {tests_displayed} measurement{'s' if tests_displayed != 1 else ''}."
    if not rows:
        rows_note = "No measurements logged in this report window yet."
    elif len(rows) > rows_limit:
        rows_note = f"Showing the newest {tests_displayed} of {len(rows)} tests."

    account_name = escape(str(account.get("name", "N/A")))
    account_number = str(account.get("number", "") or "").strip() or "N/A"
    account_provider = str(account.get("provider", "") or "").strip() or "Provider not detected yet"
    account_ip = str(account.get("ip_address", "") or "").strip() or "Not detected yet"
    account_identity = (
        f"{escape(account_provider)} · IP {escape(account_ip)} · Account {escape(account_number)}"
    )
    compliance_tone = "tone-good" if compliance >= 95 else "tone-muted" if compliance >= 80 else "tone-bad"
    total_breaches = breaches_download + breaches_upload + breaches_ping + breaches_loss
    breach_tone = "tone-good" if total_breaches == 0 else "tone-bad"
    range_summary = f"{escape(range_label)} with {len(rows)} logged test{'s' if len(rows) != 1 else ''}."

    threshold_chips = "".join(
        [
            f"<span class=\"info-chip\">DL min {_fmt(_as_number(thresholds.get('download_mbps')))} Mbps</span>",
            f"<span class=\"info-chip\">UL min {_fmt(_as_number(thresholds.get('upload_mbps')))} Mbps</span>",
            f"<span class=\"info-chip\">Ping max {_fmt(_as_number(thresholds.get('ping_ms')))} ms</span>",
            f"<span class=\"info-chip\">Loss max {_fmt(_as_number(thresholds.get('packet_loss_percent')))}%</span>",
        ]
    )
    breach_chips = "".join(
        [
            f"<span class=\"info-chip {('info-chip-good' if breaches_download == 0 else 'info-chip-bad')}\">Download {breaches_download}</span>",
            f"<span class=\"info-chip {('info-chip-good' if breaches_upload == 0 else 'info-chip-bad')}\">Upload {breaches_upload}</span>",
            f"<span class=\"info-chip {('info-chip-good' if breaches_ping == 0 else 'info-chip-bad')}\">Ping {breaches_ping}</span>",
            f"<span class=\"info-chip {('info-chip-good' if breaches_loss == 0 else 'info-chip-bad')}\">Loss {breaches_loss}</span>",
        ]
    )
    report_summary = (
        f"{len(rows)} test{'s' if len(rows) != 1 else ''} recorded · "
        f"{_fmt(compliance, 1)}% within your thresholds · "
        f"{total_breaches} breach event{'s' if total_breaches != 1 else ''}"
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
      --bg: {palette['bg']};
      --surface: {palette['surface']};
      --surface-alt: {palette['surface_alt']};
      --text: {palette['text']};
      --muted: {palette['muted']};
      --accent: {palette['accent']};
      --good: {palette['good']};
      --warn: {palette['warn']};
      --bad: {palette['bad']};
      --border: {palette['border']};
      --shadow: {palette['shadow']};
    }}
    * {{
      box-sizing: border-box;
    }}
    html {{
      font-size: 16px;
    }}
    body {{
      margin: 0;
      padding: 24px;
      background:
        radial-gradient(900px 500px at 0% 0%, color-mix(in srgb, var(--good) 10%, transparent), transparent 48%),
        radial-gradient(820px 420px at 100% 0%, color-mix(in srgb, var(--accent) 11%, transparent), transparent 44%),
        linear-gradient(180deg, color-mix(in srgb, var(--bg) 96%, black 4%), var(--bg));
      color: var(--text);
      font: 15px/1.5 "Avenir Next", "Trebuchet MS", "Segoe UI", sans-serif;
    }}
    .report-shell {{
      max-width: 1280px;
      margin: 0 auto;
    }}
    .topbar,
    .panel,
    .metric-card {{
      background:
        radial-gradient(120% 130% at 100% 0%, color-mix(in srgb, var(--accent) 10%, transparent), transparent 58%),
        linear-gradient(180deg, color-mix(in srgb, var(--surface) 96%, white 4%), color-mix(in srgb, var(--surface) 92%, var(--bg) 8%));
      border: 1px solid var(--border);
      box-shadow: var(--shadow);
    }}
    .topbar {{
      border-radius: 26px;
      padding: 22px 24px;
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      flex-wrap: wrap;
    }}
    .eyebrow {{
      margin: 0;
      font-size: 12px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
    }}
    .topbar-copy {{
      flex: 1 1 420px;
      min-width: 0;
    }}
    .topbar-copy h1,
    .panel-head h2 {{
      margin: 0;
      letter-spacing: -0.04em;
      line-height: 1.08;
      font-family: "Avenir Next", "Trebuchet MS", "Segoe UI", sans-serif;
    }}
    .topbar-copy h1 {{
      margin-top: 8px;
      font-size: clamp(2rem, 4vw, 3.3rem);
      font-weight: 820;
    }}
    .topbar-text {{
      margin: 10px 0 0;
      color: var(--muted);
      font-size: 0.96rem;
    }}
    .topbar-summary {{
      margin: 12px 0 0;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      max-width: 100%;
      padding: 8px 12px;
      border: 1px solid color-mix(in srgb, var(--border) 88%, transparent);
      border-radius: 999px;
      background: color-mix(in srgb, var(--surface-alt) 82%, transparent);
      color: var(--text);
      font-size: 0.82rem;
      font-weight: 700;
      line-height: 1.3;
      overflow-wrap: anywhere;
    }}
    .topbar-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: flex-start;
      justify-content: flex-end;
      flex: 0 1 340px;
      min-width: 0;
    }}
    .stat-pill {{
      min-width: 150px;
      padding: 12px 14px;
      border: 1px solid color-mix(in srgb, var(--border) 85%, transparent);
      border-radius: 16px;
      background: color-mix(in srgb, var(--surface-alt) 82%, transparent);
    }}
    .stat-pill span {{
      display: block;
      color: var(--muted);
      font-size: 0.76rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    .stat-pill strong {{
      display: block;
      margin-top: 6px;
      font-size: 1.5rem;
      font-weight: 820;
      letter-spacing: -0.03em;
      font-variant-numeric: tabular-nums;
    }}
    .layout-stack {{
      display: grid;
      gap: 16px;
      margin-top: 16px;
    }}
    .panel {{
      border-radius: 24px;
      padding: 20px;
      min-width: 0;
    }}
    .panel-head {{
      min-width: 0;
    }}
    .panel-head h2 {{
      font-size: clamp(1.3rem, 2.2vw, 1.8rem);
      font-weight: 780;
    }}
    .panel-subtext {{
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 0.92rem;
      overflow-wrap: anywhere;
    }}
    .panel-head-row {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 16px;
      flex-wrap: wrap;
    }}
    .hero-metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
    }}
    .metric-card {{
      border-radius: 22px;
      padding: 16px;
      min-width: 0;
    }}
    .metric-card h3 {{
      margin: 0;
      color: var(--muted);
      font-size: 0.92rem;
      font-weight: 700;
    }}
    .metric-value {{
      margin: 10px 0 4px;
      font-size: clamp(1.7rem, 2.8vw, 2.45rem);
      font-weight: 820;
      line-height: 1.1;
      letter-spacing: -0.03em;
      font-variant-numeric: tabular-nums;
    }}
    .metric-note {{
      margin: 0;
      font-size: 0.84rem;
      font-weight: 700;
    }}
    .good,
    .ok,
    .tone-good {{ color: var(--good); }}
    .bad,
    .tone-bad {{ color: var(--bad); }}
    .muted,
    .tone-muted {{ color: var(--muted); }}
    .account-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin-top: 16px;
    }}
    .account-block {{
      padding: 14px;
      border: 1px solid color-mix(in srgb, var(--border) 88%, transparent);
      border-radius: 18px;
      background: color-mix(in srgb, var(--surface-alt) 82%, transparent);
    }}
    .account-label {{
      display: block;
      color: var(--muted);
      font-size: 0.76rem;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }}
    .account-copy {{
      margin: 8px 0 0;
      font-size: 0.96rem;
    }}
    .chip-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 10px;
    }}
    .info-chip {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 7px 10px;
      border-radius: 999px;
      border: 1px solid color-mix(in srgb, var(--border) 90%, transparent);
      background: color-mix(in srgb, var(--surface) 74%, transparent);
      color: var(--text);
      font-size: 0.78rem;
      font-weight: 700;
      line-height: 1;
      white-space: nowrap;
    }}
    .info-chip-good {{
      color: var(--good);
    }}
    .info-chip-bad {{
      color: var(--bad);
    }}
    .charts-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
    }}
    .panel-wide {{
      grid-column: 1 / -1;
    }}
    .chart-wrap {{
      margin-top: 14px;
    }}
    .report-chart {{
      width: 100%;
      height: auto;
      display: block;
    }}
    .chart-frame {{
      fill: color-mix(in srgb, var(--surface-alt) 92%, transparent);
      stroke: var(--border);
    }}
    .chart-grid-line {{
      stroke: var(--border);
      stroke-width: 1;
      opacity: 0.8;
    }}
    .chart-axis {{
      stroke: var(--muted);
      stroke-width: 1.2;
      opacity: 0.75;
    }}
    .chart-axis-label {{
      fill: var(--muted);
      font-size: 11px;
      font-family: "Segoe UI", "Avenir Next", "Trebuchet MS", sans-serif;
    }}
    .chart-axis-label-y {{
      text-anchor: end;
    }}
    .chart-value-label {{
      fill: var(--text);
      font-weight: 700;
    }}
    .chart-legend {{
      margin-top: 12px;
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
    .chart-legend-swatch-trend {{
      width: 14px;
      height: 0;
      border-top: 2px dashed currentColor;
      border-radius: 0;
      background: transparent !important;
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
      color: var(--muted);
      font-size: 12px;
    }}
    .chart-note:empty {{
      display: none;
    }}
    .chart-empty {{
      min-height: 258px;
      border: 1px dashed var(--border);
      border-radius: 18px;
      display: grid;
      place-items: center;
      color: var(--muted);
      font-size: 13px;
      text-align: center;
      padding: 12px;
    }}
    .table-wrap {{
      margin-top: 14px;
      overflow-x: auto;
      border-radius: 18px;
      border: 1px solid var(--border);
      background: color-mix(in srgb, var(--surface-alt) 90%, transparent);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    th, td {{
      text-align: left;
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
      vertical-align: top;
    }}
    th {{
      background: color-mix(in srgb, var(--accent) 16%, var(--surface) 84%);
      color: var(--text);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    tr:nth-child(even) td {{
      background: color-mix(in srgb, var(--surface-alt) 72%, transparent);
    }}
    tr:last-child td {{
      border-bottom: 0;
    }}
    .foot {{
      margin-top: 12px;
      color: var(--muted);
      font-size: 12px;
      display: flex;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
    }}
    @media (max-width: 900px) {{
      body {{ padding: 12px; }}
      .topbar {{ padding: 18px; }}
      .account-grid,
      .charts-grid {{
        grid-template-columns: 1fr;
      }}
      .panel-wide {{
        grid-column: auto;
      }}
      .topbar-meta {{
        justify-content: flex-start;
      }}
    }}
  </style>
</head>
<body>
  <main class="report-shell">
    <header class="topbar">
      <div class="topbar-copy">
        <p class="eyebrow">Performance report</p>
        <h1>{escape(report_title)}</h1>
        <p class="topbar-text">{escape(range_label)} · Generated {escape(generated.strftime("%Y-%m-%d %H:%M"))}</p>
        <p class="topbar-summary">{escape(report_summary)}</p>
      </div>
      <div class="topbar-meta">
        <div class="stat-pill">
          <span>Total tests</span>
          <strong>{len(rows)}</strong>
        </div>
        <div class="stat-pill">
          <span>Compliance</span>
          <strong class="{compliance_tone}">{_fmt(compliance, 1)}%</strong>
        </div>
      </div>
    </header>

    <div class="layout-stack">
      <section class="panel account-panel">
        <div class="panel-head">
          <p class="eyebrow">Account</p>
          <h2>{account_name}</h2>
          <p class="panel-subtext">{account_identity}</p>
        </div>
        <div class="account-grid">
          <div class="account-block">
            <span class="account-label">Thresholds</span>
            <div class="chip-row">{threshold_chips}</div>
          </div>
          <div class="account-block">
            <span class="account-label">Breach counts</span>
            <div class="chip-row">{breach_chips}</div>
          </div>
          <div class="account-block">
            <span class="account-label">Latency quality</span>
            <p class="account-copy">Jitter average {_fmt(jitter_avg)} ms · Packet loss average {_fmt(loss_avg)}%</p>
          </div>
          <div class="account-block">
            <span class="account-label">Report window</span>
            <p class="account-copy">{range_summary}</p>
          </div>
        </div>
      </section>

      <section class="hero-metrics">
        <article class="metric-card">
          <h3>Download average</h3>
          <p class="metric-value">{_fmt(download_avg)} Mbps</p>
          <p class="metric-note {dl_change_tone}">{escape(dl_change_text)}</p>
        </article>
        <article class="metric-card">
          <h3>Upload average</h3>
          <p class="metric-value">{_fmt(upload_avg)} Mbps</p>
          <p class="metric-note {ul_change_tone}">{escape(ul_change_text)}</p>
        </article>
        <article class="metric-card">
          <h3>Ping average</h3>
          <p class="metric-value">{_fmt(ping_avg)} ms</p>
          <p class="metric-note {ping_change_tone}">{escape(ping_change_text)}</p>
        </article>
        <article class="metric-card">
          <h3>Total breaches</h3>
          <p class="metric-value">{total_breaches}</p>
          <p class="metric-note {breach_tone}">{'No breach events recorded' if total_breaches == 0 else 'Threshold exceptions detected'}</p>
        </article>
        <article class="metric-card">
          <h3>Average jitter</h3>
          <p class="metric-value">{_fmt(jitter_avg)} ms</p>
          <p class="metric-note tone-muted">Packet loss average {_fmt(loss_avg)}%</p>
        </article>
      </section>

      <section class="charts-grid">
        {throughput_chart}
        {latency_chart}
        {breach_chart}
      </section>

      <section class="panel results-panel">
        <div class="panel-head panel-head-row">
          <div>
            <p class="eyebrow">Measurements</p>
            <h2>Logged results</h2>
            <p class="panel-subtext">{escape(rows_note)}</p>
          </div>
          <div class="stat-pill">
            <span>Status</span>
            <strong class="{compliance_tone}">{_fmt(compliance, 1)}% within threshold</strong>
          </div>
        </div>
        <div class="table-wrap">
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
        </div>
      </section>

      <div class="foot">
        <span>{escape(range_label)} report export</span>
        <span>Generated by SpeedPulse</span>
      </div>
    </div>
  </main>
</body>
</html>
"""
