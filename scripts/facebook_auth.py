"""Inspect token metadata or explicitly provision a long-lived Page token."""
import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.facebook_auth_service import FacebookAuthError, FacebookAuthService, save_page_token


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provision', action='store_true', help='Exchange a user token and save a verified long-lived Page token locally; no upload')
    parser.add_argument('--use-configured-user-token', action='store_true', help='Use the value currently in FACEBOOK_PAGE_ACCESS_TOKEN as the USER input; type is verified before exchange')
    args = parser.parse_args()
    path = Path(__file__).resolve().parents[1] / '.env'
    load_dotenv(path)
    try:
        service = FacebookAuthService()
        if not args.provision:
            result = service.check(os.getenv('FACEBOOK_PAGE_ACCESS_TOKEN', ''))
            print(json.dumps(result, indent=2))
            if result['type'] != 'PAGE' or not result['acts_as_configured_page']:
                print('This is not a token acting as the configured Page. Use --provision after configuring the app credentials.')
        else:
            token = os.getenv('FACEBOOK_PAGE_ACCESS_TOKEN', '') if args.use_configured_user_token else getpass.getpass('Current user access token (hidden): ')
            page_token, result = service.provision(token)
            save_page_token(path, page_token)
            print(json.dumps(result, indent=2))
            print('Verified Page token saved privately in .env. Restart FastAPI/Streamlit. No upload performed.')
    except (FacebookAuthError, OSError):
        # OSError can include file contents/paths; auth errors are sanitized.
        error = sys.exc_info()[1]
        parser.exit(1, (str(error) if isinstance(error, FacebookAuthError) else 'Could not access local token configuration') + '\n')


if __name__ == '__main__':
    main()
