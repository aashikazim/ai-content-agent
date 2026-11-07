"""Offline foundation inspection; never initializes SQLite or starts services."""

import argparse
from dataclasses import asdict
import json
import logging
from pathlib import Path

from app.automation.config import load_settings
from app.automation.director import Director
from app.services.structured_logging import configure

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Lane YAML (otherwise AUTOMATION_LANES_FILE)")
    parser.add_argument("--describe", action="store_true", help="Show inert lane status; does not select topics")
    args = parser.parse_args(argv)
    configure()
    try:
        settings = load_settings(args.config)
    except (ValueError, OSError) as exc:
        logger.error("Automation configuration invalid (%s); inspect the lane file and automation settings", type(exc).__name__)
        return 2
    logger.info("Automation configuration valid: %d lanes; enabled=%s; Governor enabled=%s, dry_run=%s",
                len(settings.config.lanes), settings.enabled, settings.governor_enabled, settings.governor_dry_run)
    if args.describe:
        logger.info("Foundation inspection: %s", json.dumps(asdict(Director(settings).plan_day()), default=str))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
