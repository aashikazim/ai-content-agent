
import os
from pathlib import Path

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload"
]


def main():
    secrets_path = os.getenv(
        "YOUTUBE_CLIENT_SECRETS",
        "credentials/youtube_client_secret.json"
    )

    token_path = Path(
        os.getenv(
            "YOUTUBE_TOKEN_FILE",
            "credentials/youtube_token.json"
        )
    )

    if not Path(secrets_path).is_file():
        raise FileNotFoundError(secrets_path)

    flow = InstalledAppFlow.from_client_secrets_file(
        secrets_path,
        scopes=SCOPES
    )

    credentials = flow.run_local_server(
        host="127.0.0.1",
        port=8765,
        open_browser=False,
        access_type="offline",
        prompt="consent"
    )

    token_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    token_path.write_text(
        credentials.to_json(),
        encoding="utf-8"
    )

    token_path.chmod(0o600)

    print("YouTube OAuth authentication complete.")
    print(f"Credentials saved to: {token_path}")


if __name__ == "__main__":
    main()
