"""Release a manually inspected Facebook failure; never upload a video."""
import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.publishing_storage import PublishingStorage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publication-id', required=True)
    parser.add_argument('--confirmed-not-uploaded', action='store_true',
                        help='I verified the failed attempt did not upload/publish a Facebook video')
    parser.add_argument('--notes', required=True, help='Inspection evidence; do not include credentials')
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    storage = PublishingStorage(os.getenv('DATABASE_PATH', 'data/trends.db'))
    try:
        record = storage.authorize_retry(args.publication_id,
            confirmed_not_uploaded=args.confirmed_not_uploaded, notes=args.notes)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps({key: record[key] for key in ('publication_id', 'job_id', 'platform', 'status')}, indent=2))
    print('No upload performed. Restart the API after code/token changes, refresh Publishing, and explicitly authorize a live upload.')


if __name__ == '__main__':
    main()
