# Manages bookmarks in raindrop.io.

import argparse
import asyncio
from collections.abc import Callable
from getpass import getpass
import logging
import signal
import sys
from types import FrameType

from . import DEBUG
from .clients.raindrop import RaindropClient
from .commands.export_collection import export_collection
from .commands.maintain_collection import (
    maintain_collection,
    MaintainCollectionOptions,
)

_HOST_RATE_LIMIT_METAVAR = ("hostname", "limit", "period", "jitter")
_HOST_RATE_LIMIT_NARGS = len(_HOST_RATE_LIMIT_METAVAR)


def _sigint_handler(
    signum: int,  # noqa: ARG001
    frame: FrameType | None,  # noqa: ARG001
) -> None:
    sys.exit(130)


def _host_rate_limits_parser() -> Callable[[str], float | int | str]:
    index = -1

    def parse(value: str) -> float | int | str:
        nonlocal index

        index = (index + 1) % _HOST_RATE_LIMIT_NARGS

        match index:
            case 1:
                return int(value)
            case 2 | 3:
                return float(value)

        return value

    return parse


async def run_command(args: argparse.Namespace, raindrop_api_key: str) -> None:
    async with RaindropClient(raindrop_api_key) as raindrop_client:
        match args.command:
            case "export-collection":
                await export_collection(raindrop_client, args.collection_id)
            case "maintain-collection":
                await maintain_collection(
                    raindrop_client,
                    args.collection_id,
                    MaintainCollectionOptions(
                        args.host_rate_limits,
                        args.no_archive,
                        args.no_archive_broken,
                        args.no_checks,
                    ),
                )


def main() -> None:
    signal.signal(signal.SIGINT, _sigint_handler)

    logging.basicConfig(
        format=f"%(asctime)s:{logging.BASIC_FORMAT}",
        level=logging.WARNING if DEBUG else logging.ERROR,
    )

    arg_parser = argparse.ArgumentParser()
    subparsers = arg_parser.add_subparsers(dest="command", required=True)

    export_collection_parser = subparsers.add_parser(
        "export-collection",
        help="Exports bookmarks in HTML to stdout",
    )
    export_collection_parser.add_argument(
        "collection_id",
        help="ID of a collection to be exported",
        type=int,
    )

    maintain_collection_parser = subparsers.add_parser(
        "maintain-collection",
        help="Archives bookmarked links",
    )
    maintain_collection_parser.add_argument(
        "collection_id",
        help="ID of a collection to be maintained",
        type=int,
    )
    maintain_collection_parser.add_argument(
        "--host-rate-limit",
        action="append",
        default=[],
        dest="host_rate_limits",
        help="Sets rate limit for a hostname during link checks",
        metavar=_HOST_RATE_LIMIT_METAVAR,
        nargs=_HOST_RATE_LIMIT_NARGS,
        type=_host_rate_limits_parser(),
    )
    maintain_collection_parser.add_argument(
        "--no-archive",
        action="store_true",
        help="Disables link archiving",
    )
    maintain_collection_parser.add_argument(
        "--no-archive-broken",
        action="store_true",
        help="Disables archiving of (possibly) broken links",
    )
    maintain_collection_parser.add_argument(
        "--no-checks",
        action="store_true",
        help="Disables broken link checks",
    )

    args = arg_parser.parse_args()

    api_key = getpass("Raindrop API Key: ") if sys.stdin.isatty() else input()

    asyncio.run(run_command(args, api_key))


if __name__ == "__main__":
    main()
