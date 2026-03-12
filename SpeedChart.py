from __future__ import annotations

import base64
import datetime as dt
import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from log_parser import parse_weekly_log_file

# ── LOAD CONFIG ──────────────────────────────────────────────────────
config_path: Path = Path(__file__).parent / "config.json"
with open(config_path, 'r') as f:
    config: dict = json.load(f)

# Use yesterday's date to get the week we're reporting on
# (On Monday, this gives us last week's number)
week_num: int = (dt.date.today() - dt.timedelta(days=1)).isocalendar()[1]

# Resolve paths relative to script directory
script_dir: Path = Path(__file__).parent
LOG_PATH: Path = script_dir / config['paths']['log_directory'] / f"speed_log_week_{week_num}.txt"
IMG_DIR: Path = script_dir / config['paths']['images_directory']
B64_FILE: Path = script_dir / config['paths']['chart_base64']
Y_MAX: int = config['chart']['y_max']
THRESHOLD: int = config['thresholds']['download_mbps']
ACCOUNT_NO: str = config['account']['number']
NAME: str = config['account']['name']
# ─────────────────────────────────────────────────────────────────────

IMG_DIR.mkdir(parents=True, exist_ok=True)
IMG_PATH: Path = IMG_DIR / f"speedchart_week_{week_num}.png"

# ── PARSE LOG VIA SHARED PARSER ───────────────────────────────────────
try:
    parsed: list[dict] = parse_weekly_log_file(LOG_PATH)
except Exception as exc:
    print(f"Error parsing {LOG_PATH}: {exc}")
    exit(1)

if not parsed:
    print(f"No speed test data found in {LOG_PATH}")
    exit(1)

entries: list[dict] = [
    {
        "Datetime": e["timestamp"],
        "Download": e["download_mbps"],
        "Upload": e["upload_mbps"],
        "Ping": e["ping_ms"],
        "Jitter": e["jitter_ms"],
        "PacketLoss": e["packet_loss_percent"],
    }
    for e in parsed
]

# build DataFrame
df: pd.DataFrame = pd.DataFrame(entries).sort_values("Datetime")

if df.empty:
    print(f"No speed test data found in {LOG_PATH}")
    exit(1)

# Filter out zero values for better visualization (optional)
non_zero_df: pd.DataFrame = df[(df["Download"] > 0) | (df["Upload"] > 0)]
if non_zero_df.empty:
    print("Warning: All speed test results are 0 Mbps - using original data")
    plot_df: pd.DataFrame = df.copy()
else:
    plot_df: pd.DataFrame = non_zero_df.copy()

plot_df["DateOnly"] = plot_df["Datetime"].dt.date

# daily & weekly averages
daily: pd.DataFrame = plot_df.groupby("DateOnly").agg({"Download":"mean","Upload":"mean"}).reset_index()
weekly_dl: float = plot_df["Download"].mean()
weekly_ul: float = plot_df["Upload"].mean()
weekly_ping: float = plot_df["Ping"].mean()

# ── PLOT ─────────────────────────────────────────────────────────────
fig: Figure
ax: Axes
fig, ax = plt.subplots(figsize=(config['chart']['width'], config['chart']['height']))

# Color-code points based on threshold violations
colors_dl: list[str] = ['red' if x < THRESHOLD else 'dodgerblue' for x in plot_df["Download"]]
colors_ul: list[str] = ['red' if x < config['thresholds']['upload_mbps'] else 'seagreen' for x in plot_df["Upload"]]

# Plot with color-coded markers
for idx, row in plot_df.iterrows():
    dl_color: str = 'red' if row["Download"] < THRESHOLD else 'dodgerblue'
    ul_color: str = 'red' if row["Upload"] < config['thresholds']['upload_mbps'] else 'seagreen'
    ax.plot(row["Datetime"], row["Download"], 'o', color=dl_color, markersize=8)
    ax.plot(row["Datetime"], row["Upload"], 'o', color=ul_color, markersize=8)

# Connect points with lines
ax.plot(plot_df["Datetime"], plot_df["Download"], "-", color="dodgerblue", alpha=0.6, linewidth=2, label="Download (Mbps)")
ax.plot(plot_df["Datetime"], plot_df["Upload"], "-", color="seagreen", alpha=0.6, linewidth=2, label="Upload (Mbps)")

# Add ping line (secondary scale)
ax2: Axes = ax.twinx()
ax2.plot(plot_df["Datetime"], plot_df["Ping"], "-", color="orange", alpha=0.5, linewidth=1.5, label="Ping (ms)")
ax2.axhline(weekly_ping, color="darkorange", ls=":", linewidth=2, alpha=0.7, label=f"Avg Ping: {weekly_ping:.1f} ms")
ax2.set_ylabel("Ping (ms)", color="orange")
ax2.tick_params(axis='y', labelcolor="orange")
ax2.set_ylim(0, 50)  # Adjust based on typical ping values

# Weekly ping average label on the right Y-axis
ax2.text(1.02, weekly_ping/50, f"AVG: {weekly_ping:.1f} ms",
        fontsize=9, color="darkorange", ha="left", va="center", weight='bold',
        transform=ax2.transAxes)

# Min/Max markers
dl_min_idx = plot_df["Download"].idxmin()
dl_max_idx = plot_df["Download"].idxmax()
ul_min_idx = plot_df["Upload"].idxmin()
ul_max_idx = plot_df["Upload"].idxmax()

ax.plot(plot_df.loc[dl_max_idx, "Datetime"], plot_df.loc[dl_max_idx, "Download"],
        marker='*', markersize=15, color='darkblue', zorder=5)
ax.plot(plot_df.loc[dl_min_idx, "Datetime"], plot_df.loc[dl_min_idx, "Download"],
        marker='v', markersize=12, color='navy', zorder=5)
ax.plot(plot_df.loc[ul_max_idx, "Datetime"], plot_df.loc[ul_max_idx, "Upload"],
        marker='*', markersize=15, color='darkgreen', zorder=5)
ax.plot(plot_df.loc[ul_min_idx, "Datetime"], plot_df.loc[ul_min_idx, "Upload"],
        marker='v', markersize=12, color='darkgreen', zorder=5)

# threshold & weekly lines
ax.axhline(THRESHOLD, color="red", ls="--", linewidth=2, label=f"Threshold ({THRESHOLD} Mbps)")
ax.axhline(weekly_dl, color="gray", ls=":", linewidth=2, label=f"Weekly Avg DL: {weekly_dl:.0f} Mbps")
ax.axhline(weekly_ul, color="dimgray", ls="-.", linewidth=2, label=f"Weekly Avg UP: {weekly_ul:.0f} Mbps")

# Weekly average labels on the Y-axis (left margin)
ax.text(-0.02, weekly_dl/Y_MAX, f"AVG DL: {weekly_dl:.0f}",
        fontsize=9, color="gray", ha="right", va="center", weight='bold',
        transform=ax.transAxes)
ax.text(-0.02, weekly_ul/Y_MAX, f"AVG UP: {weekly_ul:.0f}",
        fontsize=9, color="dimgray", ha="right", va="center", weight='bold',
        transform=ax.transAxes)

# midnight separators
for d in sorted(plot_df["DateOnly"].unique())[1:]:
    midnight_dt = dt.datetime.combine(d, dt.time())
    ax.axvline(midnight_dt, color="#CCCCCC", ls="--", lw=1)

# Individual speed labels at each data point
for idx, row in plot_df.iterrows():
    # Download label above the point
    ax.text(row["Datetime"], row["Download"]+20, f"DL: {row['Download']:.0f}",
            fontsize=7, color="dodgerblue", ha="center", va="bottom")
    # Upload label below the point
    ax.text(row["Datetime"], row["Upload"]-20, f"UP: {row['Upload']:.0f}",
            fontsize=7, color="seagreen", ha="center", va="top")

# Add packet loss indicators if available
if "PacketLoss" in plot_df.columns:
    for idx, row in plot_df.iterrows():
        if row["PacketLoss"] > config['thresholds']['packet_loss_percent']:
            # Show warning icon for packet loss
            ax.text(row["Datetime"], Y_MAX - 50, "⚠",
                    fontsize=12, color="red", ha="center", va="top")

# X-ticks at 08:00,16:00,22:00 with bold date under 16:00
xticks: list[dt.datetime] = []
xlabels: list[str] = []
for ts in plot_df["Datetime"]:
    t: str = ts.strftime("%H:%M")
    if t in ("08:00","16:00","22:00"):
        xticks.append(ts)
        if t=="16:00":
            lbl = f"{t}\n$\\bf{{{ts.strftime('%d-%m')}}}$"
        else:
            lbl = t
        xlabels.append(lbl)
ax.set_xticks(xticks)
ax.set_xticklabels(xlabels, fontsize=9, ha="center")

# styling
ax.set_title(f"Weekly Internet Speed – {NAME} (Acc. {ACCOUNT_NO})  Week {week_num}", fontsize=14, weight='bold')
ax.set_ylabel("Speed (Mbps)", fontsize=11)
ax.set_ylim(0, Y_MAX)
ax.grid(axis="y", color="#EEEEEE", alpha=0.7)

# Combine legends from both axes
lines1, labels1 = ax.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
ax.legend(lines1 + lines2, labels1 + labels2, loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=6, framealpha=0.9, fontsize=9)
plt.tight_layout()

# save PNG
fig.savefig(IMG_PATH, dpi=config['chart']['dpi'], bbox_inches="tight")

# write Base64
with open(IMG_PATH, "rb") as imgf:
    b64 = base64.b64encode(imgf.read()).decode()
with open(B64_FILE, "w", encoding="utf-8") as fh:
    fh.write(b64)

print("Chart written to", IMG_PATH)
