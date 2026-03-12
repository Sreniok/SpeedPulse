#!/usr/bin/env python3
"""
update_credentials.py - Set up encrypted email credentials
Replaces Update-Credentials.ps1 for Linux/Ubuntu

DEPRECATED: This script is legacy code for non-Docker deployments.
Docker deployments use .env environment variables instead.
See .env.example for the recommended credential configuration.
"""

import getpass
import json
import smtplib
import sys
from pathlib import Path

from credentials_manager import CredentialsManager


def load_config():
    """Load configuration from config.json"""
    config_path = Path(__file__).parent / "config.json"
    if not config_path.exists():
        print("❌ config.json not found!")
        sys.exit(1)

    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def test_smtp_connection(smtp_server, smtp_port, email_user, email_pass):
    """Test SMTP connection"""
    try:
        if smtp_port == 465:
            # SSL connection
            server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=10)
        else:
            # STARTTLS connection
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=10)
            server.starttls()

        server.login(email_user, email_pass)
        server.quit()
        return True, "Connection successful"
    except Exception as e:
        return False, str(e)


def main():
    print("\n=== Speedtest Monitor - Credential Setup (Ubuntu) ===")
    print("This script will encrypt your credentials using Fernet encryption\n")

    # Load config to get default email
    config = load_config()
    default_email = config['email']['from']
    default_smtp_server = config['email']['smtp_server']
    default_smtp_port = config['email']['smtp_port']

    # Check if credentials already exist
    manager = CredentialsManager()
    if manager.credentials_exist():
        print("⚠️  Existing encrypted credentials found\n")
        confirm = input("Do you want to update the credentials? (yes/no): ").strip().lower()
        if confirm not in ['yes', 'y']:
            print("\n❌ Operation cancelled. Existing credentials were not modified.")
            sys.exit(0)
        print()

    # Get email username
    print(f"📧 Email address from config.json: {default_email}")
    use_default = input("   Use this email? (yes/no) [yes]: ").strip().lower()
    if use_default in ['no', 'n']:
        email_user = input("   Enter email address: ").strip()
    else:
        email_user = default_email

    # Get email password
    print("\n🔑 Enter your Email Password:")
    print("   (characters will be hidden for security)")
    print("   For Gmail: Use App Password from https://myaccount.google.com/apppasswords")
    email_pass = getpass.getpass("   Password: ")

    if not email_pass:
        print("\n❌ Password cannot be empty!")
        sys.exit(1)

    # Get SMTP server
    print(f"\n🌐 SMTP Server from config.json: {default_smtp_server}")
    use_default_smtp = input("   Use this server? (yes/no) [yes]: ").strip().lower()
    if use_default_smtp in ['no', 'n']:
        smtp_server = input("   Enter SMTP server: ").strip()
    else:
        smtp_server = default_smtp_server

    # Get SMTP port
    print(f"\n🔌 SMTP Port from config.json: {default_smtp_port}")
    print("   Port 465 = SSL, Port 587 = STARTTLS")
    use_default_port = input("   Use this port? (yes/no) [yes]: ").strip().lower()
    if use_default_port in ['no', 'n']:
        try:
            smtp_port = int(input("   Enter SMTP port: ").strip())
        except ValueError:
            print("❌ Invalid port number!")
            sys.exit(1)
    else:
        smtp_port = default_smtp_port

    # Test connection
    print("\n🔍 Testing SMTP connection...")
    success, message = test_smtp_connection(smtp_server, smtp_port, email_user, email_pass)

    if not success:
        print(f"❌ SMTP connection test failed: {message}")
        print("\n⚠️  Credentials will still be saved, but please verify your settings.")
        confirm = input("   Continue anyway? (yes/no): ").strip().lower()
        if confirm not in ['yes', 'y']:
            sys.exit(1)
    else:
        print("✓ SMTP connection test successful!")

    # Save encrypted credentials
    print("\n💾 Saving encrypted credentials...")
    manager.save_credentials(email_user, email_pass, smtp_server, smtp_port)

    print("\n✅ SUCCESS! Credentials have been encrypted and saved.")
    print("   Files created:")
    print(f"   - {manager.credentials_file.name}")
    print(f"   - {manager.key_file.name} (encryption key)")
    print("\n🔒 Security:")
    print("   - Files are encrypted and protected (chmod 600)")
    print("   - Add to .gitignore to prevent accidental commits")
    print("\n📝 Configuration:")
    print(f"   Email: {email_user}")
    print(f"   SMTP: {smtp_server}:{smtp_port}")
    print("\n✓ You can now run speed tests and send reports!")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n❌ Operation cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)
