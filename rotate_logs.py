#!/usr/bin/env python3
"""
Log Rotation Script
Manages log file retention and cleanup for the Speed Test Monitor.

Retention Policy:
- Speed test logs: Keep 12 months (52 weeks)
- Error logs: Keep 30 days
- Cron logs: Keep 30 days
- Archived logs: Compress and keep for reference
"""

import glob
import gzip
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from config_loader import load_json_config, resolve_runtime_path

# Configuration
SCRIPT_DIR = Path(__file__).parent.absolute()
LOG_DIR = resolve_runtime_path(__file__, "Log")
IMAGES_DIR = resolve_runtime_path(__file__, "Images")
ARCHIVE_DIR = resolve_runtime_path(__file__, "Archive")

# Load retention settings: env var > config.json > hardcoded default
def _load_retention() -> tuple[int, int]:
    keep_weeks = 52
    keep_days = 30
    try:
        cfg = load_json_config(__file__)
        keep_weeks = cfg.get("data_retention", {}).get("keep_weeks", keep_weeks)
        keep_days = cfg.get("data_retention", {}).get("keep_days", keep_days)
    except (json.JSONDecodeError, OSError):
        pass
    keep_weeks = int(os.getenv("KEEP_WEEKS", str(keep_weeks)))
    keep_days = int(os.getenv("KEEP_DAYS", str(keep_days)))
    return keep_weeks, keep_days

KEEP_WEEKS, KEEP_DAYS = _load_retention()

# Colors for output
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
RED = '\033[0;31m'
BLUE = '\033[0;34m'
NC = '\033[0m'  # No Color


def print_header():
    """Print script header."""
    print(f"\n{BLUE}{'=' * 50}{NC}")
    print(f"{BLUE}  Log Rotation & Cleanup{NC}")
    print(f"{BLUE}{'=' * 50}{NC}\n")


def print_success(msg):
    """Print success message."""
    print(f"{GREEN}✓{NC} {msg}")


def print_info(msg):
    """Print info message."""
    print(f"{BLUE}ℹ{NC} {msg}")


def print_warning(msg):
    """Print warning message."""
    print(f"{YELLOW}⚠{NC} {msg}")


def print_error(msg):
    """Print error message."""
    print(f"{RED}✗{NC} {msg}")


def get_week_number_from_filename(filename):
    """Extract week number from log filename."""
    try:
        # Extract week number from format: speed_log_week_XX.txt
        week = int(filename.split('_')[-1].replace('.txt', ''))
        return week
    except (ValueError, IndexError):
        return None


def get_current_week():
    """Get current ISO week number."""
    return datetime.now().isocalendar()[1]


def rotate_speed_logs():
    """Archive speed test logs older than 12 months (52 weeks)."""
    print_info("Rotating speed test logs...")

    if not LOG_DIR.exists():
        print_warning("Log directory not found")
        return

    # Create archive directory if it doesn't exist
    ARCHIVE_DIR.mkdir(exist_ok=True)

    current_week = get_current_week()
    archived_count = 0

    # Get all log files
    log_files = glob.glob(str(LOG_DIR / "speed_log_week_*.txt"))

    for log_file in log_files:
        filename = os.path.basename(log_file)
        week_num = get_week_number_from_filename(filename)

        if week_num is None:
            continue

        # Calculate age (handling year rollover)
        if week_num > current_week:
            # Week from previous year
            age_weeks = (52 - week_num) + current_week
        else:
            age_weeks = current_week - week_num

        # Archive logs older than KEEP_WEEKS
        if age_weeks > KEEP_WEEKS:
            archive_path = ARCHIVE_DIR / f"{filename}.gz"

            try:
                # Compress and archive
                with open(log_file, 'rb') as f_in:
                    with gzip.open(archive_path, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)

                # Remove original
                os.remove(log_file)
                archived_count += 1
                print(f"  Archived: {filename} (Week {week_num}, {age_weeks} weeks old)")

            except Exception as e:
                print_error(f"Failed to archive {filename}: {e}")

    if archived_count == 0:
        print_success("No logs need archiving (all within 12 months)")
    else:
        print_success(f"Archived {archived_count} log file(s)")


def rotate_images():
    """Archive chart images older than 12 months."""
    print_info("Rotating chart images...")

    if not IMAGES_DIR.exists():
        print_warning("Images directory not found")
        return

    ARCHIVE_DIR.mkdir(exist_ok=True)
    image_archive = ARCHIVE_DIR / "images"
    image_archive.mkdir(exist_ok=True)

    current_week = get_current_week()
    archived_count = 0

    # Get all chart files
    image_files = glob.glob(str(IMAGES_DIR / "speedchart_week_*.png"))

    for image_file in image_files:
        filename = os.path.basename(image_file)

        # Extract week number
        try:
            week_num = int(filename.split('_')[-1].replace('.png', ''))
        except (ValueError, IndexError):
            continue

        # Calculate age
        if week_num > current_week:
            age_weeks = (52 - week_num) + current_week
        else:
            age_weeks = current_week - week_num

        # Archive images older than KEEP_WEEKS
        if age_weeks > KEEP_WEEKS:
            archive_path = image_archive / filename

            try:
                shutil.move(image_file, archive_path)
                archived_count += 1
                print(f"  Archived: {filename}")
            except Exception as e:
                print_error(f"Failed to archive {filename}: {e}")

    if archived_count == 0:
        print_success("No images need archiving (all within 12 months)")
    else:
        print_success(f"Archived {archived_count} image(s)")


def rotate_error_log():
    """Rotate error log if older than 30 days."""
    print_info("Rotating error log...")

    config = {}
    try:
        config = load_json_config(__file__)
    except (json.JSONDecodeError, OSError):
        config = {}
    error_log = resolve_runtime_path(__file__, config.get("paths", {}).get("error_log", "errors.log"))

    if not error_log.exists():
        print_warning("Error log not found")
        return

    # Check file age
    file_time = datetime.fromtimestamp(error_log.stat().st_mtime)
    age_days = (datetime.now() - file_time).days

    if age_days > KEEP_DAYS:
        # Archive old log
        ARCHIVE_DIR.mkdir(exist_ok=True)
        timestamp = file_time.strftime("%Y%m%d")
        archive_path = ARCHIVE_DIR / f"errors_{timestamp}.log.gz"

        try:
            with open(error_log, 'rb') as f_in:
                with gzip.open(archive_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)

            # Clear the log file
            with open(error_log, 'w') as f:
                f.write(f"# Error log rotated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

            print_success(f"Rotated error log ({age_days} days old)")
        except Exception as e:
            print_error(f"Failed to rotate error log: {e}")
    else:
        print_success(f"Error log is recent ({age_days} days old)")


def rotate_cron_log():
    """Rotate cron log if larger than 10MB or older than 30 days."""
    print_info("Rotating cron log...")

    cron_log = resolve_runtime_path(__file__, "cron.log")

    if not cron_log.exists():
        print_warning("Cron log not found")
        return

    # Check file size and age
    file_size_mb = cron_log.stat().st_size / (1024 * 1024)
    file_time = datetime.fromtimestamp(cron_log.stat().st_mtime)
    age_days = (datetime.now() - file_time).days

    should_rotate = file_size_mb > 10 or age_days > KEEP_DAYS

    if should_rotate:
        ARCHIVE_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d")
        archive_path = ARCHIVE_DIR / f"cron_{timestamp}.log.gz"

        try:
            with open(cron_log, 'rb') as f_in:
                with gzip.open(archive_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)

            # Clear the log file
            with open(cron_log, 'w') as f:
                f.write(f"# Cron log rotated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

            reason = f"{file_size_mb:.1f}MB" if file_size_mb > 10 else f"{age_days} days old"
            print_success(f"Rotated cron log ({reason})")
        except Exception as e:
            print_error(f"Failed to rotate cron log: {e}")
    else:
        print_success(f"Cron log is recent ({age_days} days, {file_size_mb:.1f}MB)")


def show_summary():
    """Show storage summary."""
    print(f"\n{BLUE}{'=' * 50}{NC}")
    print(f"{BLUE}  Storage Summary{NC}")
    print(f"{BLUE}{'=' * 50}{NC}\n")

    # Count active logs
    if LOG_DIR.exists():
        log_count = len(list(LOG_DIR.glob("speed_log_week_*.txt")))
        print(f"Active speed logs: {log_count} files")

    # Count active images
    if IMAGES_DIR.exists():
        image_count = len(list(IMAGES_DIR.glob("speedchart_week_*.png")))
        print(f"Active chart images: {image_count} files")

    # Count archived items
    if ARCHIVE_DIR.exists():
        archived_logs = len(list(ARCHIVE_DIR.glob("speed_log_week_*.txt.gz")))
        archived_images = len(list((ARCHIVE_DIR / "images").glob("*.png"))) if (ARCHIVE_DIR / "images").exists() else 0
        print(f"Archived logs: {archived_logs} files")
        print(f"Archived images: {archived_images} files")

    print()


def main():
    """Main execution."""
    print_header()

    print("Retention policy:")
    print(f"  - Speed test logs: {KEEP_WEEKS} weeks (12 months)")
    print(f"  - Error/Cron logs: {KEEP_DAYS} days")
    print()

    # Rotate logs
    rotate_speed_logs()
    rotate_images()
    rotate_error_log()
    rotate_cron_log()

    # Show summary
    show_summary()

    print_success("Log rotation complete!\n")


if __name__ == "__main__":
    main()
