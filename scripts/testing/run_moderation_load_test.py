from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts.load_testing.moderation_load_test_config import ModerationLoadTestConfig
from src.infrastructure.logging import get_logger
from src.modules.load_testing.moderation_api_load_test_runner import ModerationApiLoadTestRunner

logger = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load test the local Muxivo Core API.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--channels", type=int, default=20)
    parser.add_argument("--users", type=int, default=100)
    parser.add_argument("--messages-per-user", type=int, default=5)
    parser.add_argument("--duration-seconds", type=float, default=60.0)
    parser.add_argument("--max-in-flight", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--min-success-rate", type=float, default=0.99)
    parser.add_argument("--max-p95-latency-ms", type=float, default=5_000.0)
    parser.add_argument("--max-p80-latency-ms", type=float, default=3_000.0)
    return parser.parse_args()


async def run() -> int:
    args = parse_args()
    try:
        config = ModerationLoadTestConfig(
            base_url=args.base_url,
            channel_count=args.channels,
            user_count=args.users,
            messages_per_user=args.messages_per_user,
            duration_seconds=args.duration_seconds,
            max_in_flight=args.max_in_flight,
            request_timeout_seconds=args.timeout_seconds,
            min_success_rate=args.min_success_rate,
            max_p95_latency_ms=args.max_p95_latency_ms,
            max_p80_latency_ms=args.max_p80_latency_ms,
        )
    except ValidationError as exc:
        logger.error("Load test configuration is invalid errors=%s", exc.error_count())
        return 2

    api_key = os.environ.get("MUXIVO_CORE_INTERNAL_API_KEY", "")
    if not api_key:
        logger.error("Load test rejected because MUXIVO_CORE_INTERNAL_API_KEY is not configured")
        return 2

    result = await ModerationApiLoadTestRunner(config, internal_api_key=api_key).run()
    sys.stdout.write(result.model_dump_json(indent=2) + "\n")
    return 0 if result.targets_met else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
