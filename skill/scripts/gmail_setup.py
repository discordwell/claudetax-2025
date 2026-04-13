"""Interactive Gmail API setup for tax-prep scan-email.

Guides the user through enabling the Gmail API and creating OAuth2
credentials. Automates via ``gcloud`` CLI when available, falls back
to step-by-step browser instructions.

Usage::

    tax-prep setup-gmail
    # or
    python -m skill.scripts.gmail_setup
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

_DEFAULT_CREDENTIALS_PATH = Path.home() / ".tax-prep" / "client_secret.json"
_PROJECT_ID = "tax-prep-skill"


def _has_gcloud() -> bool:
    return shutil.which("gcloud") is not None


def _run(cmd: list[str], check: bool = True, capture: bool = True) -> str:
    """Run a shell command and return stdout."""
    result = subprocess.run(
        cmd, capture_output=capture, text=True, check=check
    )
    return result.stdout.strip() if capture else ""


def _gcloud_auth_check() -> bool:
    """Check if gcloud is authenticated."""
    try:
        account = _run(["gcloud", "config", "get-value", "account"])
        return bool(account and account != "(unset)")
    except subprocess.CalledProcessError:
        return False


def _gcloud_setup() -> Path | None:
    """Automated setup via gcloud CLI."""
    print("Found gcloud CLI. Attempting automated setup...\n")

    if not _gcloud_auth_check():
        print("gcloud is not authenticated. Please run:")
        print("  gcloud auth login")
        print("\nThen re-run: tax-prep setup-gmail")
        return None

    # Check if project exists, create if not
    try:
        _run(["gcloud", "projects", "describe", _PROJECT_ID])
        print(f"Project '{_PROJECT_ID}' already exists.")
    except subprocess.CalledProcessError:
        print(f"Creating project '{_PROJECT_ID}'...")
        try:
            _run(["gcloud", "projects", "create", _PROJECT_ID,
                  "--name=Tax Prep Skill"])
        except subprocess.CalledProcessError as e:
            print(f"Could not create project: {e}")
            print("You may need to create it manually at:")
            print("  https://console.cloud.google.com/projectcreate")
            return None

    _run(["gcloud", "config", "set", "project", _PROJECT_ID])

    # Enable Gmail API
    print("Enabling Gmail API...")
    try:
        _run(["gcloud", "services", "enable", "gmail.googleapis.com"])
        print("Gmail API enabled.")
    except subprocess.CalledProcessError:
        print("Could not enable Gmail API automatically.")
        print("Enable it manually at:")
        print(f"  https://console.cloud.google.com/apis/library/gmail.googleapis.com?project={_PROJECT_ID}")
        return None

    # Configure OAuth consent screen (required before creating credentials)
    print("\nNote: You need to configure the OAuth consent screen before")
    print("creating credentials. If you haven't done this yet:")
    print(f"  https://console.cloud.google.com/apis/credentials/consent?project={_PROJECT_ID}")
    print("  - Choose 'External' user type")
    print("  - App name: Tax Prep Skill")
    print("  - Add scope: https://www.googleapis.com/auth/gmail.readonly")
    print("  - Add your email as a test user")

    # Create OAuth client ID
    print("\nCreating OAuth2 Desktop credentials...")
    try:
        result = _run([
            "gcloud", "alpha", "iap", "oauth-clients", "create",
            "--display-name=Tax Prep CLI",
            "--type=DESKTOP",
        ])
    except (subprocess.CalledProcessError, FileNotFoundError):
        # gcloud alpha may not be available, fall back to manual
        pass

    # The gcloud CLI doesn't reliably create OAuth2 desktop credentials,
    # so direct the user to the console for this step.
    print("\nFinal step — create the OAuth2 client ID:")
    print(f"  1. Go to: https://console.cloud.google.com/apis/credentials?project={_PROJECT_ID}")
    print("  2. Click '+ CREATE CREDENTIALS' > 'OAuth client ID'")
    print("  3. Application type: 'Desktop app'")
    print("  4. Name: 'Tax Prep CLI'")
    print("  5. Click 'CREATE', then 'DOWNLOAD JSON'")
    print(f"  6. Save the downloaded file to: {_DEFAULT_CREDENTIALS_PATH}")
    print(f"\nOr pass the path via: tax-prep scan-email --credentials <path>")

    return None


def _manual_setup() -> None:
    """Print step-by-step manual setup instructions."""
    print("=" * 60)
    print("  Gmail API Setup for tax-prep scan-email")
    print("=" * 60)
    print()
    print("This is a one-time setup. Follow these steps:")
    print()
    print("Step 1: Create a Google Cloud Project")
    print("  - Go to: https://console.cloud.google.com/projectcreate")
    print("  - Project name: Tax Prep Skill")
    print("  - Click 'CREATE'")
    print()
    print("Step 2: Enable the Gmail API")
    print("  - Go to: https://console.cloud.google.com/apis/library/gmail.googleapis.com")
    print("  - Click 'ENABLE'")
    print()
    print("Step 3: Configure OAuth Consent Screen")
    print("  - Go to: https://console.cloud.google.com/apis/credentials/consent")
    print("  - Choose 'External' > 'CREATE'")
    print("  - App name: Tax Prep Skill")
    print("  - Support email: your email")
    print("  - Click 'SAVE AND CONTINUE' through all steps")
    print("  - Under 'Test users', add your Gmail address")
    print("  - Click 'SAVE AND CONTINUE'")
    print()
    print("Step 4: Create OAuth2 Credentials")
    print("  - Go to: https://console.cloud.google.com/apis/credentials")
    print("  - Click '+ CREATE CREDENTIALS' > 'OAuth client ID'")
    print("  - Application type: 'Desktop app'")
    print("  - Name: 'Tax Prep CLI'")
    print("  - Click 'CREATE'")
    print("  - Click 'DOWNLOAD JSON'")
    print(f"  - Save the file to: {_DEFAULT_CREDENTIALS_PATH}")
    print()
    print("Step 5: Run the scanner")
    print(f"  tax-prep scan-email --credentials {_DEFAULT_CREDENTIALS_PATH} --output ./tax_pdfs")
    print()
    print("On first run, a browser window will open for you to")
    print("authorize Gmail read-only access. The token is cached")
    print("at ~/.tax-prep/gmail_token.json for future runs.")
    print()
    print("=" * 60)


def setup() -> int:
    """Run the interactive setup flow. Returns exit code."""
    # Check if credentials already exist
    if _DEFAULT_CREDENTIALS_PATH.exists():
        print(f"Credentials already exist at {_DEFAULT_CREDENTIALS_PATH}")
        print("You're ready to scan:")
        print(f"  tax-prep scan-email --credentials {_DEFAULT_CREDENTIALS_PATH} --output ./tax_pdfs")
        return 0

    _DEFAULT_CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)

    if _has_gcloud():
        _gcloud_setup()
    else:
        _manual_setup()

    return 0


if __name__ == "__main__":
    sys.exit(setup())
