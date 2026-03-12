#!/usr/bin/env python3
"""
credentials_manager.py - Cross-platform credential encryption
Replaces Windows DPAPI with cryptography.fernet for Linux/Ubuntu

DEPRECATED: This module is legacy code for non-Docker deployments.
Docker deployments use .env environment variables instead.
See .env.example for the recommended credential configuration.
"""

import json
import os
from pathlib import Path

from cryptography.fernet import Fernet


class CredentialsManager:
    """Manage encrypted credentials using Fernet encryption"""

    def __init__(self, script_dir=None):
        if script_dir is None:
            self.script_dir = Path(__file__).parent
        else:
            self.script_dir = Path(script_dir)

        self.key_file = self.script_dir / ".encryption_key"
        self.credentials_file = self.script_dir / "credentials.enc"
        self._ensure_key()

    def _ensure_key(self):
        """Ensure encryption key exists, create if not"""
        if not self.key_file.exists():
            key = Fernet.generate_key()
            # Save with restricted permissions (user read/write only)
            self.key_file.write_bytes(key)
            os.chmod(self.key_file, 0o600)

    def _get_cipher(self):
        """Get Fernet cipher instance"""
        key = self.key_file.read_bytes()
        return Fernet(key)

    def save_credentials(self, email_user, email_pass, smtp_server=None, smtp_port=None):
        """Encrypt and save credentials"""
        credentials = {
            'email_user': email_user,
            'email_pass': email_pass,
            'smtp_server': smtp_server or 'smtp.gmail.com',
            'smtp_port': smtp_port or 587
        }

        cipher = self._get_cipher()
        encrypted_data = cipher.encrypt(json.dumps(credentials).encode('utf-8'))

        # Save with restricted permissions
        self.credentials_file.write_bytes(encrypted_data)
        os.chmod(self.credentials_file, 0o600)

    def load_credentials(self):
        """Load and decrypt credentials"""
        if not self.credentials_file.exists():
            raise FileNotFoundError(
                f"Credentials file not found: {self.credentials_file}\n"
                "Run update_credentials.py first to set up credentials."
            )

        cipher = self._get_cipher()
        encrypted_data = self.credentials_file.read_bytes()
        decrypted_data = cipher.decrypt(encrypted_data)
        return json.loads(decrypted_data.decode('utf-8'))

    def get_email_user(self):
        """Get email username"""
        return self.load_credentials()['email_user']

    def get_email_pass(self):
        """Get email password"""
        return self.load_credentials()['email_pass']

    def get_smtp_server(self):
        """Get SMTP server"""
        return self.load_credentials()['smtp_server']

    def get_smtp_port(self):
        """Get SMTP port"""
        return self.load_credentials()['smtp_port']

    def credentials_exist(self):
        """Check if credentials are configured"""
        return self.credentials_file.exists() and self.key_file.exists()


# Convenience functions for backward compatibility
def load_credentials():
    """Load credentials - main entry point"""
    manager = CredentialsManager()
    return manager.load_credentials()


def get_email_user():
    """Get email username"""
    manager = CredentialsManager()
    return manager.get_email_user()


def get_email_pass():
    """Get email password"""
    manager = CredentialsManager()
    return manager.get_email_pass()


if __name__ == "__main__":
    # Test functionality
    manager = CredentialsManager()
    if manager.credentials_exist():
        print("✓ Credentials found")
        creds = manager.load_credentials()
        print(f"  Email: {creds['email_user']}")
        print(f"  SMTP: {creds['smtp_server']}:{creds['smtp_port']}")
    else:
        print("✗ No credentials found. Run update_credentials.py")
