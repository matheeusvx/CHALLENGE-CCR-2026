"""Execute one scheduler-ready monitoring batch and exit."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime

from apps.api.app.routes.analyses import run_persisted_road_section
from src.satellite_monitoring.database import (
    MonitoringConfig,
    MonitoringService,
    init_database,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--road")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = MonitoringConfig.from_env()
    if args.limit is not None and args.limit <= 0:
        raise SystemExit("--limit must be positive")
    init_database()
    current = datetime.now(UTC)
    service = MonitoringService(
        lambda section: run_persisted_road_section(section, now=current),
        config=config,
    )
    due = service.due_sections(now=current, limit=args.limit, road=args.road)
    if args.dry_run:
        print(json.dumps({
            "dry_run": True,
            "due": len(due),
            "sections": [
                {
                    "subject_key": item.subject_key,
                    "overdue_seconds": item.overdue_seconds,
                    "road_ref": item.road_ref,
                    "section_id": item.section_id,
                }
                for item in due
            ],
        }, ensure_ascii=False, indent=2))
    summary = service.run_once(
        now=current, dry_run=args.dry_run, road=args.road, limit=args.limit
    )
    print(json.dumps(summary.to_dict(), sort_keys=True))
    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
