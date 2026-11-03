
import argparse
import json
import os

from dotenv import load_dotenv
from app.services.publishing_service import PublishingService

load_dotenv()


def main():
    parser = argparse.ArgumentParser(
        description="Publish an approved AI video"
    )

    parser.add_argument(
        "--job-id",
        required=True
    )

    parser.add_argument(
        "--platform",
        choices=["youtube", "facebook"],
        required=True
    )

    parser.add_argument(
        "--live",
        action="store_true",
        help="Perform a real platform upload"
    )

    args = parser.parse_args()

    service = PublishingService()

    result = service.publish(
        job_id=args.job_id,
        platform=args.platform,
        dry_run=not args.live
    )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
