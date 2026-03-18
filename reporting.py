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
