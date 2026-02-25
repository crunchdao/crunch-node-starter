"""Manual historical backfill for feed data.

Usage:
    python scripts/backfill.py --source binance --subject BTC --kind candle --granularity 1m \
        --from 2026-01-01 --to 2026-02-01

Or via make:
    make backfill FROM=2026-01-01 TO=2026-02-01
    make backfill FROM=2026-01-01 TO=2026-02-01 SOURCE=binance SUBJECT=BTCUSDT KIND=candle GRANULARITY=1m
"""

import os
import sys

# Ensure app root is on sys.path when running as a script inside Docker
_app_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _app_dir not in sys.path:
    sys.path.insert(0, _app_dir)

import argparse
import asyncio
import logging
from datetime import UTC, datetime

from coordinator_node.db import create_session
from coordinator_node.db.feed_records import DBFeedRecordRepository
from coordinator_node.feeds import create_default_registry
from coordinator_node.services.backfill import BackfillRequest, BackfillService


def parse_datetime(value):
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.replace(tzinfo=UTC)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        f"Cannot parse datetime: {value!r} (expected YYYY-MM-DD or ISO format)"
    )


def main():
    parser = argparse.ArgumentParser(description="Backfill data from a feed provider")
    parser.add_argument(
        "--source",
        default=os.getenv("FEED_SOURCE", os.getenv("FEED_PROVIDER", "pyth")),
        help="Feed source (default: $FEED_SOURCE or pyth)",
    )
    parser.add_argument(
        "--subject",
        default=os.getenv("FEED_SUBJECTS", os.getenv("FEED_ASSETS", "BTC")),
        help="Subject(s), comma-separated (default: $FEED_SUBJECTS or BTC)",
    )
    parser.add_argument(
        "--kind",
        default=os.getenv("FEED_KIND", "tick"),
        help="Data kind: tick or candle (default: $FEED_KIND or tick)",
    )
    parser.add_argument(
        "--granularity",
        default=os.getenv("FEED_GRANULARITY", "1s"),
        help="Granularity (default: $FEED_GRANULARITY or 1s)",
    )
    parser.add_argument(
        "--from",
        dest="start",
        required=True,
        type=parse_datetime,
        help="Start date (YYYY-MM-DD or ISO)",
    )
    parser.add_argument(
        "--to",
        dest="end",
        required=True,
        type=parse_datetime,
        help="End date (YYYY-MM-DD or ISO)",
    )
    parser.add_argument(
        "--page-size", type=int, default=500, help="Records per page (default: 500)"
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        force=True,
    )
    logger = logging.getLogger("backfill")

    subjects = tuple(s.strip() for s in args.subject.split(",") if s.strip())

    logger.info(
        "backfill starting source=%s subjects=%s kind=%s granularity=%s from=%s to=%s",
        args.source,
        ",".join(subjects),
        args.kind,
        args.granularity,
        args.start.isoformat(),
        args.end.isoformat(),
    )

    registry = create_default_registry()
    feed = registry.create_from_env(default_provider=args.source)

    session = create_session()
    repo = DBFeedRecordRepository(session)

    request = BackfillRequest(
        source=args.source,
        subjects=subjects,
        kind=args.kind,
        granularity=args.granularity,
        start=args.start,
        end=args.end,
        page_size=args.page_size,
    )

    result = asyncio.run(BackfillService(feed=feed, repository=repo).run(request))

    logger.info(
        "backfill complete records_written=%d pages_fetched=%d",
        result.records_written,
        result.pages_fetched,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
