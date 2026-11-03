"""Configure the single administrator from a local terminal, never over HTTP."""

import argparse
import getpass
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app.services.admin_auth_service import AdminAuthService


def main():
    parser = argparse.ArgumentParser(description="Configure the single dashboard administrator")
    parser.add_argument("--username", default="admin", help="Administrator username (default: admin)")
    parser.add_argument("--reset", action="store_true", help="Replace the existing account and revoke all sessions")
    arguments = parser.parse_args()
    try:
        service = AdminAuthService()
        if service.credentials_path.exists() and not arguments.reset:
            parser.error("Administrator already configured; use --reset to replace it")
        password = getpass.getpass("New administrator password (at least 12 characters): ")
        confirmation = getpass.getpass("Confirm administrator password: ")
        if password != confirmation:
            parser.error("Passwords do not match")
        service.configure_admin(arguments.username, password, reset=arguments.reset)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print("Administrator account configured. Existing browser sessions have been revoked.")


if __name__ == "__main__":
    main()
