#!/usr/bin/env python3
"""
Clean Slate Script
Clears all logs and images when switching ISP providers.
Keeps configuration and scripts intact.

USE WITH CAUTION: This will delete all historical data!
"""

import shutil
from datetime import datetime
from pathlib import Path

# Configuration
SCRIPT_DIR = Path(__file__).parent.absolute()
LOG_DIR = SCRIPT_DIR / "Log"
IMAGES_DIR = SCRIPT_DIR / "Images"
ARCHIVE_DIR = SCRIPT_DIR / "Archive"

# Files to clean
ERROR_LOG = SCRIPT_DIR / "errors.log"
CRON_LOG = SCRIPT_DIR / "cron.log"
LAST_ALERT = SCRIPT_DIR / "last_alert.txt"

# Colors
RED = '\033[0;31m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
BLUE = '\033[0;34m'
NC = '\033[0m'


def print_header():
    """Print warning header."""
    print(f"\n{RED}{'=' * 60}{NC}")
    print(f"{RED}  ⚠️  CLEAN SLATE - DATA DELETION TOOL  ⚠️{NC}")
    print(f"{RED}{'=' * 60}{NC}\n")


def print_warning(msg):
    """Print warning message."""
    print(f"{YELLOW}⚠{NC} {msg}")


def print_info(msg):
    """Print info message."""
    print(f"{BLUE}ℹ{NC} {msg}")


def print_success(msg):
    """Print success message."""
    print(f"{GREEN}✓{NC} {msg}")


def confirm_deletion():
    """Ask user to confirm deletion."""
    print(f"{YELLOW}This will DELETE:{NC}")
    print("  • All speed test logs")
    print("  • All chart images")
    print("  • All archived data")
    print("  • Error logs")
    print("  • Cron logs")
    print("  • Alert history")
    print()
    print(f"{GREEN}This will KEEP:{NC}")
    print("  • Configuration (config.json)")
    print("  • Email credentials")
    print("  • All Python scripts")
    print()

    response = input(f"{RED}Are you ABSOLUTELY SURE you want to proceed? Type 'DELETE' to confirm: {NC}")

    return response.strip() == "DELETE"


def backup_config():
    """Create a backup of config.json."""
    config_file = SCRIPT_DIR / "config.json"

    if config_file.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = SCRIPT_DIR / f"config_backup_{timestamp}.json"

        try:
            shutil.copy2(config_file, backup_file)
            print_success(f"Config backed up: {backup_file.name}")
            return True
        except Exception as e:
            print_warning(f"Could not backup config: {e}")
            return False

    return True


def clean_directory(directory, description):
    """Clean a directory."""
    print_info(f"Cleaning {description}...")

    if not directory.exists():
        print_warning(f"{description} directory not found")
        return 0

    file_count = 0

    try:
        for item in directory.iterdir():
            if item.is_file():
                item.unlink()
                file_count += 1
                print(f"  Deleted: {item.name}")
            elif item.is_dir():
                shutil.rmtree(item)
                file_count += 1
                print(f"  Deleted directory: {item.name}")

        print_success(f"Cleaned {file_count} item(s) from {description}")
        return file_count

    except Exception as e:
        print_warning(f"Error cleaning {description}: {e}")
        return 0


def clean_file(file_path, description):
    """Clean a single file."""
    if file_path.exists():
        try:
            file_path.unlink()
            print_success(f"Deleted {description}")
            return True
        except Exception as e:
            print_warning(f"Could not delete {description}: {e}")
            return False
    else:
        print_info(f"{description} not found (already clean)")
        return True


def create_fresh_readme():
    """Create a README in cleaned directories."""
    for directory in [LOG_DIR, IMAGES_DIR]:
        directory.mkdir(exist_ok=True)
        readme = directory / "README.md"

        with open(readme, 'w') as f:
            f.write(f"# {directory.name}\n\n")
            f.write(f"Cleaned on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("\nThis directory will store speed test data.\n")

        print_success(f"Created fresh {directory.name} directory")


def main():
    """Main execution."""
    print_header()

    print("This tool is designed for when you:")
    print("  • Switch to a new ISP provider")
    print("  • Want to start fresh with clean logs")
    print("  • Need to remove all historical data")
    print()

    # Confirm deletion
    if not confirm_deletion():
        print()
        print_info("Operation cancelled. No files were deleted.")
        print()
        return

    print()
    print(f"{BLUE}{'=' * 60}{NC}")
    print(f"{BLUE}  Starting clean slate process...{NC}")
    print(f"{BLUE}{'=' * 60}{NC}\n")

    # Backup config first
    backup_config()
    print()

    # Clean directories
    total_deleted = 0
    total_deleted += clean_directory(LOG_DIR, "speed test logs")
    total_deleted += clean_directory(IMAGES_DIR, "chart images")
    total_deleted += clean_directory(ARCHIVE_DIR, "archived data")

    # Clean individual files
    clean_file(ERROR_LOG, "error log")
    clean_file(CRON_LOG, "cron log")
    clean_file(LAST_ALERT, "last alert file")

    # Create fresh directories
    print()
    create_fresh_readme()

    # Summary
    print()
    print(f"{GREEN}{'=' * 60}{NC}")
    print(f"{GREEN}  Clean slate complete!{NC}")
    print(f"{GREEN}{'=' * 60}{NC}\n")

    print(f"Total items deleted: {total_deleted}")
    print()
    print("Your system is now ready for a fresh start!")
    print()
    print("Next steps:")
    print("  1. Update config.json with new ISP thresholds (if needed)")
    print("  2. Run a test: python3 CheckSpeed.py")
    print("  3. Verify cron jobs are still active: crontab -l")
    print()


if __name__ == "__main__":
    main()
