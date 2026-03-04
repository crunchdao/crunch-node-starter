from __future__ import annotations

import asyncio
import logging

from crunch_node.db import DBFeedRecordRepository, create_session
from crunch_node.services.feed_data import FeedDataService, FeedDataSettings


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        force=True,
    )


def build_service() -> FeedDataService:
    settings = FeedDataSettings.from_env()
    session = create_session()
    return FeedDataService(
        settings=settings,
        feed_record_repository=DBFeedRecordRepository(session),
    )


async def main() -> None:
    configure_logging()
    logger = logging.getLogger(__name__)
    logger.info("coordinator feed-data worker bootstrap")

    service = build_service()
    await service.run()


if __name__ == "__main__":
    asyncio.run(main())
