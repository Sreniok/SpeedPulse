#!/usr/bin/env python3
"""
Annual Summary Report Generator
Generates a comprehensive 12-month performance report with statistics and charts.
Run this at the end of your ISP contract period to get a full year summary.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))
from log_parser import load_all_log_entries

try:
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    import pandas as pd
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure
except ImportError as e:
    print(f"Error: Missing required package: {e}")
    print("Install with: pip3 install pandas matplotlib")
    sys.exit(1)


# Configuration
SCRIPT_DIR: Path = Path(__file__).parent.absolute()
LOG_DIR: Path = SCRIPT_DIR / "Log"
IMAGES_DIR: Path = SCRIPT_DIR / "Images"
CONFIG_FILE: Path = SCRIPT_DIR / "config.json"

# Colors
GREEN = '\033[0;32m'
BLUE = '\033[0;34m'
YELLOW = '\033[1;33m'
NC = '\033[0m'


def print_header() -> None:
    """Print script header."""
    print(f"\n{BLUE}{'=' * 60}{NC}")
    print(f"{BLUE}  SpeedPulse — Annual Summary Report Generator{NC}")
    print(f"{BLUE}{'=' * 60}{NC}\n")


def print_success(msg: str) -> None:
    """Print success message."""
    print(f"{GREEN}✓{NC} {msg}")


def print_info(msg: str) -> None:
    """Print info message."""
    print(f"{BLUE}ℹ{NC} {msg}")


def load_config() -> dict:
    """Load configuration."""
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: Could not load config: {e}")
        return {}


def load_all_logs() -> pd.DataFrame:
    """Load all available log files."""
    print_info("Loading log files...")

    all_entries: list[dict] = load_all_log_entries(LOG_DIR)
    if not all_entries:
        print("No valid data found in logs!")
        return pd.DataFrame()

    all_data: list[dict] = [
        {
            'timestamp': entry['timestamp'],
            'download': entry['download_mbps'],
            'upload': entry['upload_mbps'],
            'ping': entry['ping_ms'],
            'jitter': entry['jitter_ms'],
            'packet_loss': entry['packet_loss_percent'],
        }
        for entry in all_entries
    ]

    df: pd.DataFrame = pd.DataFrame(all_data)
    if df.empty:
        print("No valid data found after conversion!")
        return df

    df = df.sort_values('timestamp')

    log_files: list[Path] = sorted(LOG_DIR.glob("speed_log_week_*.txt"))
    print_success(f"Loaded {len(df)} speed tests from {len(log_files)} log files")

    # Show date range
    first_test: datetime = df['timestamp'].min()
    last_test: datetime = df['timestamp'].max()
    days: int = (last_test - first_test).days
    print(f"  Date range: {first_test.strftime('%Y-%m-%d')} to {last_test.strftime('%Y-%m-%d')} ({days} days)")

    return df


def calculate_statistics(df: pd.DataFrame, config: dict) -> dict:
    """Calculate annual statistics."""
    print_info("Calculating statistics...")

    thresholds: dict = config.get('thresholds', {})
    download_threshold: float = thresholds.get('download_mbps', 500)
    upload_threshold: float = thresholds.get('upload_mbps', 80)
    ping_threshold: float = thresholds.get('ping_ms', 20)

    stats: dict = {
        'total_tests': len(df),
        'download': {
            'mean': df['download'].mean(),
            'median': df['download'].median(),
            'min': df['download'].min(),
            'max': df['download'].max(),
            'std': df['download'].std(),
            'below_threshold': (df['download'] < download_threshold).sum(),
            'threshold': download_threshold
        },
        'upload': {
            'mean': df['upload'].mean(),
            'median': df['upload'].median(),
            'min': df['upload'].min(),
            'max': df['upload'].max(),
            'std': df['upload'].std(),
            'below_threshold': (df['upload'] < upload_threshold).sum(),
            'threshold': upload_threshold
        },
        'ping': {
            'mean': df['ping'].mean(),
            'median': df['ping'].median(),
            'min': df['ping'].min(),
            'max': df['ping'].max(),
            'std': df['ping'].std(),
            'above_threshold': (df['ping'] > ping_threshold).sum(),
            'threshold': ping_threshold
        },
        'jitter': {
            'mean': df['jitter'].mean(),
            'median': df['jitter'].median(),
            'max': df['jitter'].max()
        },
        'packet_loss': {
            'mean': df['packet_loss'].mean(),
            'median': df['packet_loss'].median(),
            'max': df['packet_loss'].max(),
            'incidents': (df['packet_loss'] > 0).sum()
        }
    }

    # Calculate reliability score
    total_violations: int = (stats['download']['below_threshold'] +
                       stats['upload']['below_threshold'] +
                       stats['ping']['above_threshold'])
    stats['reliability_score'] = ((stats['total_tests'] - total_violations) / stats['total_tests'] * 100)

    # Grade
    score: float = stats['reliability_score']
    if score >= 95:
        grade: str = 'A'
    elif score >= 90:
        grade = 'B'
    elif score >= 80:
        grade = 'C'
    elif score >= 70:
        grade = 'D'
    else:
        grade = 'F'

    stats['grade'] = grade

    return stats


def print_statistics(stats: dict, config: dict) -> None:
    """Print statistics to console."""
    print(f"\n{BLUE}{'=' * 60}{NC}")
    print(f"{BLUE}  Annual Performance Summary{NC}")
    print(f"{BLUE}{'=' * 60}{NC}\n")

    account: dict = config.get('account', {})
    print(f"Account: {account.get('name', 'N/A')} (#{account.get('number', 'N/A')})")
    print(f"Report Period: {datetime.now().year}")
    print(f"Total Tests: {stats['total_tests']}")
    print()

    print(f"{BLUE}Download Speed:{NC}")
    print(f"  Average: {stats['download']['mean']:.1f} Mbps")
    print(f"  Median:  {stats['download']['median']:.1f} Mbps")
    print(f"  Range:   {stats['download']['min']:.1f} - {stats['download']['max']:.1f} Mbps")
    print(f"  Below threshold ({stats['download']['threshold']} Mbps): {stats['download']['below_threshold']} times")
    print()

    print(f"{BLUE}Upload Speed:{NC}")
    print(f"  Average: {stats['upload']['mean']:.1f} Mbps")
    print(f"  Median:  {stats['upload']['median']:.1f} Mbps")
    print(f"  Range:   {stats['upload']['min']:.1f} - {stats['upload']['max']:.1f} Mbps")
    print(f"  Below threshold ({stats['upload']['threshold']} Mbps): {stats['upload']['below_threshold']} times")
    print()

    print(f"{BLUE}Latency:{NC}")
    print(f"  Average Ping: {stats['ping']['mean']:.1f} ms")
    print(f"  Average Jitter: {stats['jitter']['mean']:.1f} ms")
    print(f"  Ping above threshold ({stats['ping']['threshold']} ms): {stats['ping']['above_threshold']} times")
    print()

    print(f"{BLUE}Reliability:{NC}")
    print(f"  Packet Loss Incidents: {stats['packet_loss']['incidents']}")
    print(f"  Reliability Score: {stats['reliability_score']:.1f}%")
    print(f"  Overall Grade: {stats['grade']}")
    print()


def generate_annual_chart(df: pd.DataFrame, config: dict, stats: dict) -> Path:
    """Generate annual performance chart."""
    print_info("Generating annual chart...")

    IMAGES_DIR.mkdir(exist_ok=True)

    fig: Figure
    ax1: Axes
    ax2: Axes
    ax3: Axes
    ax4: Axes
    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 12))
    fig.suptitle(f'SpeedPulse — Annual Summary - {datetime.now().year}',
                 fontsize=16, fontweight='bold')

    # 1. Download/Upload over time
    ax1.plot(df['timestamp'], df['download'], 'b-', alpha=0.6, label='Download')
    ax1.plot(df['timestamp'], df['upload'], 'g-', alpha=0.6, label='Upload')

    thresholds: dict = config.get('thresholds', {})
    ax1.axhline(y=thresholds.get('download_mbps', 500), color='b', linestyle='--', alpha=0.3, label='Download Threshold')
    ax1.axhline(y=thresholds.get('upload_mbps', 80), color='g', linestyle='--', alpha=0.3, label='Upload Threshold')

    ax1.set_title('Speed Over Time', fontweight='bold')
    ax1.set_xlabel('Date')
    ax1.set_ylabel('Speed (Mbps)')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%b'))

    # 2. Monthly averages
    df['month'] = df['timestamp'].dt.to_period('M')
    monthly: pd.DataFrame = df.groupby('month').agg({
        'download': 'mean',
        'upload': 'mean'
    })

    months: list[str] = [str(m) for m in monthly.index]
    x: range = range(len(months))

    ax2.bar([i - 0.2 for i in x], monthly['download'], width=0.4, label='Download', color='blue', alpha=0.7)
    ax2.bar([i + 0.2 for i in x], monthly['upload'], width=0.4, label='Upload', color='green', alpha=0.7)

    ax2.set_title('Monthly Average Speeds', fontweight='bold')
    ax2.set_xlabel('Month')
    ax2.set_ylabel('Speed (Mbps)')
    ax2.set_xticks(x)
    ax2.set_xticklabels(months, rotation=45)
    ax2.legend()
    ax2.grid(True, alpha=0.3, axis='y')

    # 3. Ping/Jitter over time
    ax3.plot(df['timestamp'], df['ping'], 'r-', alpha=0.6, label='Ping')
    ax3.plot(df['timestamp'], df['jitter'], 'orange', alpha=0.6, label='Jitter')
    ax3.axhline(y=thresholds.get('ping_ms', 20), color='r', linestyle='--', alpha=0.3, label='Ping Threshold')

    ax3.set_title('Latency Over Time', fontweight='bold')
    ax3.set_xlabel('Date')
    ax3.set_ylabel('Latency (ms)')
    ax3.legend()
    ax3.grid(True, alpha=0.3)
    ax3.xaxis.set_major_formatter(mdates.DateFormatter('%b'))

    # 4. Performance summary
    ax4.axis('off')

    summary_text = f"""
    ANNUAL PERFORMANCE SUMMARY
    
    Total Tests: {stats['total_tests']}
    
    Download Speed
      • Average: {stats['download']['mean']:.1f} Mbps
      • Best: {stats['download']['max']:.1f} Mbps
      • Worst: {stats['download']['min']:.1f} Mbps
    
    Upload Speed
      • Average: {stats['upload']['mean']:.1f} Mbps
      • Best: {stats['upload']['max']:.1f} Mbps
      • Worst: {stats['upload']['min']:.1f} Mbps
    
    Latency
      • Avg Ping: {stats['ping']['mean']:.1f} ms
      • Avg Jitter: {stats['jitter']['mean']:.1f} ms
    
    Reliability Score: {stats['reliability_score']:.1f}%
    Overall Grade: {stats['grade']}
    """

    ax4.text(0.1, 0.5, summary_text, fontsize=11, verticalalignment='center',
             family='monospace', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.3))

    plt.tight_layout()

    # Save chart
    chart_filename: str = f"annual_summary_{datetime.now().year}.png"
    chart_path: Path = IMAGES_DIR / chart_filename
    plt.savefig(chart_path, dpi=150, bbox_inches='tight')
    plt.close()

    print_success(f"Chart saved: {chart_filename}")

    return chart_path


def main() -> None:
    print_header()

    # Load config
    config = load_config()

    # Load all log data
    df = load_all_logs()

    if df.empty:
        print("No data available for annual report!")
        return

    # Calculate statistics
    stats = calculate_statistics(df, config)

    # Print to console
    print_statistics(stats, config)

    # Generate chart
    chart_path = generate_annual_chart(df, config, stats)

    print(f"\n{GREEN}{'=' * 60}{NC}")
    print(f"{GREEN}  Annual report generated successfully!{NC}")
    print(f"{GREEN}{'=' * 60}{NC}\n")

    print(f"Chart saved to: {chart_path}")
    print()
    print("Use this report when:")
    print("  • Negotiating with your ISP")
    print("  • Deciding whether to renew your contract")
    print("  • Comparing ISP performance")
    print("  • Filing complaints about service quality")
    print()


if __name__ == "__main__":
    main()
