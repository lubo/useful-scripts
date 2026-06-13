import asyncio
import contextlib
from contextlib import AbstractContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
from functools import partial
from http import HTTPStatus
from io import BytesIO
from typing import cast, NoReturn, TYPE_CHECKING
import urllib.parse

import aiohttp
from tqdm import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm
from yarl import URL

from bookmarkmgr import cronet, scraper
from bookmarkmgr.asyncio import ForgivingTaskGroup
from bookmarkmgr.checks.duplicate_link import (
    DuplicateLinkChecker,
    get_canonical_url,
)
from bookmarkmgr.checks.link_status import (
    check_link_status,
    get_fixed_url,
    LinkStatus,
)
from bookmarkmgr.clients import imgpile
from bookmarkmgr.clients.archive_today import (
    ArchiveTodayClient,
    ArchiveTodayError,
)
from bookmarkmgr.clients.raindrop import (
    RaindropClient,
    RaindropIn,
    RaindropOut,
)
from bookmarkmgr.clients.wayback_machine import (
    WaybackMachineClient,
    WaybackMachineError,
)
from bookmarkmgr.collections import TypedDefaultsDict
from bookmarkmgr.cronet import PerHostnameRateLimitedSession
from bookmarkmgr.logging import get_logger
from bookmarkmgr.types import Failure, Result, Success
from bookmarkmgr.utils.link_metadata import (
    Metadata,
    metadata_from_note,
    metadata_to_note,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable


logger = get_logger()

BROKEN_LINK_STATUSES = {
    LinkStatus.BROKEN,
    LinkStatus.POSSIBLY_BROKEN,
}

LINK_STATUS_TAGS = {
    LinkStatus.BROKEN: "broken",
    LinkStatus.POSSIBLY_BROKEN: "possibly-broken",
    LinkStatus.BLOCKED: "blocked",
}


@dataclass(frozen=True)
class MaintainCollectionOptions:
    host_rate_limits: list[tuple[str, int, float, float]]
    no_archive: bool
    no_archive_broken: bool
    no_checks: bool


@asynccontextmanager
async def as_async[T](
    context_manager: AbstractContextManager[T],
) -> AsyncIterator[T]:
    with context_manager as value:
        yield value


@asynccontextmanager
async def get_progress_bar(
    description: str,
    *,
    leave: bool = True,
) -> AsyncIterator[tqdm[NoReturn]]:
    stop_refreshing = False

    with tqdm(
        desc=description,
        dynamic_ncols=True,
        leave=leave,
        unit="links",
    ) as progress_bar:
        # Custom refresh task is used because tqdm cannot refresh the progress
        # bar every second.
        # https://github.com/tqdm/tqdm/issues/861
        progress_bar.__class__.monitor_interval = 0

        async def refresh() -> None:
            while True:
                if stop_refreshing:
                    break

                progress_bar.refresh()

                await asyncio.sleep(1)

        refresh_task = asyncio.create_task(refresh())
        try:
            yield progress_bar
        finally:
            stop_refreshing = True
            await refresh_task


async def process_archival_result(
    result_awaitable: Awaitable[Result[str, str]],
    raindrop: RaindropIn,
    metadata: Metadata,
    service_initials: str,
    service_name: str,
) -> None:
    result: Result[str, str] | ArchiveTodayError | WaybackMachineError

    try:
        result = await result_awaitable
    except (ArchiveTodayError, WaybackMachineError) as e:
        result = e

    match result:
        case Success(archival_url):
            metadata[f"Archive ({service_initials})"] = (
                f"[link]({archival_url})"
            )
            tag = "archived"
        case _:
            match result:
                case Failure(message):
                    error = message
                case _:
                    error = result

            logger.error(
                "Archival in %s failed: %s: %s",
                service_name,
                error,
                raindrop["link"],
            )

            if isinstance(error, Exception):
                return

            metadata[f"Archival Error ({service_initials})"] = error
            tag = "archival-failed"

    if tag in raindrop["tags"]:
        return

    raindrop["tags"] = [*raindrop["tags"], tag]


def create_archival_tasks(  # noqa: PLR0913
    task_group: asyncio.TaskGroup,
    raindrop: RaindropIn,
    note_metadata: Metadata,
    at_client: ArchiveTodayClient,
    wm_client: WaybackMachineClient,
    user_options: MaintainCollectionOptions,
) -> list[asyncio.Task[None]]:
    is_link_broken = (
        len(
            {"broken", "possibly-broken"}.intersection(raindrop["tags"]),
        )
        > 0
    )
    link = raindrop["link"]
    tasks = []

    if not (
        user_options.no_archive
        or (user_options.no_archive_broken and is_link_broken)
        or note_metadata.get("Archive (AT)")
        or note_metadata.get("Archival Error (AT)")
    ):
        tasks.append(
            task_group.create_task(
                process_archival_result(
                    at_client.archive_page(link),
                    raindrop,
                    note_metadata,
                    "AT",
                    "archive.today",
                ),
                name=f"Archive-Today-{link}",
            ),
        )

    if not (
        user_options.no_archive
        or (user_options.no_archive_broken and is_link_broken)
        or note_metadata.get("Archive (WM)")
        or note_metadata.get("Archival Error (WM)")
    ):
        tasks.append(
            task_group.create_task(
                process_archival_result(
                    wm_client.archive_page(link),
                    raindrop,
                    note_metadata,
                    "WM",
                    "Wayback Machine",
                ),
                name=f"Wayback-Machine-{link}",
            ),
        )

    return tasks


def add_or_remove_tag(
    raindrop: RaindropIn,
    add_tag: bool,  # noqa: FBT001
    tag: str,
    error: str | None = None,
) -> None:
    tag_added = tag in raindrop["tags"]

    if not add_tag and not tag_added:
        return

    link = raindrop["link"]

    if not add_tag and tag_added:
        logger.info("Removing #%s on %s", tag, link)

        raindrop["tags"] = [t for t in raindrop["tags"] if t != tag]

        return

    error_message = link if error is None else f"{error}: {link}"

    if add_tag and tag_added:
        logger.debug("Link still %s: %s", tag, error_message)

        return

    logger.info("%s link found: %s", tag.capitalize(), error_message)

    raindrop["tags"] = [*raindrop["tags"], tag]


async def scrape_and_check(
    session: cronet.RetrySession,
    url: str,
) -> tuple[scraper.Page | None, LinkStatus, str | None, str | None]:
    scraper_result = await scraper.scrape_page(session, url)
    link_status, error = check_link_status(scraper_result)

    if isinstance(scraper_result, cronet.RequestError):
        return None, link_status, error, None

    if (fixed_url := get_fixed_url(scraper_result.response, url)) is None:
        return scraper_result.page, link_status, error, None

    old_page = scraper_result.page

    scraper_result = await scraper.scrape_page(session, fixed_url)
    fixed_link_status, fixed_error = check_link_status(scraper_result)

    if (
        isinstance(scraper_result, scraper.ScrapedData)
        and fixed_link_status == LinkStatus.OK
    ):
        return scraper_result.page, fixed_link_status, fixed_error, fixed_url

    return old_page, link_status, error, None


async def process_scrape_and_check_result(  # noqa: C901, PLR0912, PLR0913, PLR0915
    check_session: cronet.RetrySession,
    raindrop: RaindropIn,
    metadata: Metadata,
    imgpile_client: imgpile.Client,
    archival_tasks: list[asyncio.Task[None]],
    create_archival_tasks: Callable[[asyncio.TaskGroup], object],
) -> None:
    url = raindrop["link"]
    page, link_status, error, fixed_url = await scrape_and_check(
        check_session,
        url,
    )
    now = datetime.now(tz=UTC)

    metadata["Last check"] = str(now)

    if fixed_url is not None:
        logger.info("Fixing URL to %s", fixed_url)

        raindrop["link"] = url = fixed_url

    og_image = None

    if link_status == LinkStatus.OK:
        if page is None:
            message = "Page is None"
            raise ValueError(message)

        if (
            canonical_url := get_canonical_url(
                page,
                metadata.get("Canonical URL") or url,
            )
        ) is not None:
            if canonical_url == url:
                metadata.pop("Canonical URL", None)
            else:
                metadata["Canonical URL"] = canonical_url

        # Raindrop sometimes fails to extract the cover image automatically.
        if page.og_image:
            og_image = page.og_image

    if link_status in BROKEN_LINK_STATUSES:
        try:
            broken_since = datetime.fromisoformat(
                metadata.get("Broken since", ""),
            ).replace(tzinfo=UTC)
        except ValueError:
            broken_since = now
            metadata["Broken since"] = str(broken_since)

        if link_status == LinkStatus.POSSIBLY_BROKEN and (
            now >= broken_since + timedelta(days=7)
        ):
            link_status = LinkStatus.BROKEN
    else:
        metadata.pop("Broken since", None)

    for status, tag in LINK_STATUS_TAGS.items():
        add_or_remove_tag(
            raindrop,
            link_status == status,
            tag,
            error,
        )

    if fixed_url is not None:
        for task in archival_tasks:
            task.cancel()

        for task in archival_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

        for key in [
            "Archive (AT)",
            "Archival Error (AT)",
            "Archive (WM)",
            "Archival Error (WM)",
        ]:
            metadata.pop(key, None)

    cover_candidates = []
    if (
        "cover-mirrored" not in raindrop["tags"]
        and "cover-mirroring-failed" not in raindrop["tags"]
    ):
        if raindrop["cover"]:
            cover_candidates += [
                raindrop["cover"],
                "https://rdl.ink/render/"
                + urllib.parse.quote(raindrop["cover"], safe=""),
            ]

        if og_image:
            og_image_url = URL(og_image)
            if not og_image_url.host:
                og_image_url = URL(url).join(og_image_url)
            og_image_url = og_image_url.with_scheme("https")

            cover_candidates += [
                str(og_image_url),
            ]

        if not raindrop["cover"]:
            cover_candidates += [
                "https://rdl.ink/render/" + urllib.parse.quote(url, safe=""),
            ]

    async with ForgivingTaskGroup() as task_group:
        if fixed_url is not None:
            create_archival_tasks(task_group)

        if cover_candidates:
            _: asyncio.Task[None] = task_group.create_task(
                mirror_raindrop_cover(
                    check_session,
                    raindrop,
                    imgpile_client,
                    cover_candidates,
                ),
                name=f"Mirror-Cover-{url}",
            )


async def mirror_raindrop_cover(
    check_session: cronet.RetrySession,
    raindrop: RaindropIn,
    imgpile_client: imgpile.Client,
    candidate_urls: list[str],
) -> None:
    result: (
        imgpile.MediaUploadResult | aiohttp.ClientError | cronet.RequestError
    )

    for url in candidate_urls:
        try:
            # ruff: disable[FIX002, TD002, TD003]
            image_response = await check_session.get(
                url,
                # TODO: Remove when redirects are supported.
                allow_redirects=False,
            )
            # ruff: enable[FIX002, TD002, TD003]

            if image_response.status_code != HTTPStatus.OK:
                continue

            result = await imgpile_client.media_upload(
                BytesIO(image_response.content),
            )
        except (aiohttp.ClientError, cronet.RequestError) as exc:
            result = exc

        match result:
            case Success(response):
                # Mypy currently doesn't support type narrowing here.
                response = cast("imgpile.MediaCreatedResponse", response)

                raindrop["cover"] = response["media"]["urls"]["original"]
                raindrop["tags"] = [*raindrop["tags"], "cover-mirrored"]

                logger.info(
                    "Cover image mirrored successfully: %s",
                    raindrop["link"],
                )

                return
            case _:
                match result:
                    case Failure(response):
                        error = response
                    case _:
                        error = result

                logger.error(
                    "Failed to mirror cover image: %s: %s: %s",
                    error,
                    url,
                    raindrop["link"],
                )

                return

    raindrop["tags"] = [*raindrop["tags"], "cover-mirroring-failed"]

    logger.error(
        "Mirroring all cover images candidates failed: %s",
        raindrop["link"],
    )


async def process_check_duplicate_result(
    result_awaitable: Awaitable[bool],
    raindrop: RaindropIn,
) -> None:
    add_or_remove_tag(
        raindrop,
        await result_awaitable,
        "duplicate",
    )


def create_raindrop_maintenance_tasks(  # noqa: PLR0913
    task_group: asyncio.TaskGroup,
    raindrop_with_defauls: TypedDefaultsDict[RaindropIn, RaindropOut],
    note_metadata: Metadata,
    at_client: ArchiveTodayClient,
    imgpile_client: imgpile.Client,
    wm_client: WaybackMachineClient,
    check_session: cronet.RetrySession,
    duplicate_checker: DuplicateLinkChecker,
    user_options: MaintainCollectionOptions,
) -> None:
    raindrop = raindrop_with_defauls.to_typeddict()

    create_archival_tasks_partial = partial(
        create_archival_tasks,
        raindrop=raindrop,
        note_metadata=note_metadata,
        at_client=at_client,
        wm_client=wm_client,
        user_options=user_options,
    )
    link = raindrop["link"]

    try:
        last_check = datetime.fromisoformat(
            note_metadata.get("Last check", ""),
        ).replace(tzinfo=UTC)
    except ValueError:
        last_check = datetime.fromtimestamp(0, tz=UTC)

    archival_tasks = create_archival_tasks_partial(task_group)

    if (
        not user_options.no_checks
        and (
            "broken" not in raindrop["tags"]
            or datetime.now(tz=UTC) > last_check + timedelta(weeks=1)
        )
        and (
            "blocked" in raindrop["tags"]
            or (
                "cover-mirrored" not in raindrop["tags"]
                and "cover-mirroring-failed" not in raindrop["tags"]
            )
            or "possibly-broken" in raindrop["tags"]
            or datetime.now(tz=UTC) > last_check + timedelta(days=1)
        )
    ):
        _: asyncio.Task[None] = task_group.create_task(
            process_scrape_and_check_result(
                check_session,
                raindrop,
                note_metadata,
                imgpile_client,
                archival_tasks,
                create_archival_tasks_partial,
            ),
            name=f"Scrape-And-Check-{link}",
        )

    if not user_options.no_checks:
        canonical_url_raindrop: RaindropOut = {
            **raindrop_with_defauls.defaults,
            "link": note_metadata.get("Canonical URL") or link,
        }

        duplicate_checker.add_link(canonical_url_raindrop)

        _ = task_group.create_task(
            process_check_duplicate_result(
                duplicate_checker.is_link_duplicate(
                    canonical_url_raindrop,
                ),
                raindrop,
            ),
            name=f"Check-Is-Duplicate-{link}",
        )


async def maintain_raindrop(  # noqa: PLR0913
    raindrop_client: RaindropClient,
    raindrop: RaindropOut,
    at_client: ArchiveTodayClient,
    imgpile_client: imgpile.Client,
    wm_client: WaybackMachineClient,
    check_session: cronet.RetrySession,
    duplicate_checker: DuplicateLinkChecker,
    user_options: MaintainCollectionOptions,
) -> None:
    note_metadata = metadata_from_note(raindrop["note"])
    task_group_error = None
    updated_raindrop_with_defauls = TypedDefaultsDict[RaindropIn, RaindropOut](
        raindrop,
    )

    try:
        async with ForgivingTaskGroup() as task_group:
            create_raindrop_maintenance_tasks(
                task_group,
                updated_raindrop_with_defauls,
                note_metadata,
                at_client,
                imgpile_client,
                wm_client,
                check_session,
                duplicate_checker,
                user_options,
            )
    except ExceptionGroup as error:
        task_group_error = error

    try:
        updated_raindrop = updated_raindrop_with_defauls.to_typeddict()

        if raindrop["note"] != (new_note := metadata_to_note(note_metadata)):
            updated_raindrop["note"] = new_note

        if updated_raindrop["tags"] != (
            sorted_tags := sorted(updated_raindrop["tags"])
        ):
            updated_raindrop["tags"] = sorted_tags

        if not updated_raindrop_with_defauls:
            return

        await raindrop_client.update_raindrop(
            raindrop["_id"],
            updated_raindrop_with_defauls.data,
        )
    finally:
        if task_group_error is not None:
            raise task_group_error


async def maintain_collection(
    raindrop_client: RaindropClient,
    collection_id: int,
    imgpile_api_key: str,
    user_options: MaintainCollectionOptions,
) -> None:
    items = raindrop_client.get_collection_items(collection_id)

    duplicate_checker = DuplicateLinkChecker()

    async with (
        as_async(logging_redirect_tqdm()),
        get_progress_bar(
            "Maintaining",
        ) as maintaining_progress_bar,
        ArchiveTodayClient() as at_client,
        imgpile.Client(imgpile_api_key) as imgpile_client,
        WaybackMachineClient() as wm_client,
        PerHostnameRateLimitedSession(
            host_rate_limits=user_options.host_rate_limits,
        ) as check_session,
        ForgivingTaskGroup() as task_group,
        get_progress_bar(
            "  Loading",
            leave=False,
        ) as loading_progress_bar,
    ):

        def on_task_done(
            task: asyncio.Task[object],  # noqa: ARG001
        ) -> None:
            maintaining_progress_bar.update(1)

        async for item in items:
            loading_progress_bar.update(1)

            task = task_group.create_task(
                maintain_raindrop(
                    raindrop_client,
                    item,
                    at_client,
                    imgpile_client,
                    wm_client,
                    check_session,
                    duplicate_checker,
                    user_options,
                ),
                name=f"Maintain-{item['link']}",
            )
            task.add_done_callback(on_task_done)

            await asyncio.sleep(0)

        maintaining_progress_bar.total = loading_progress_bar.n

        duplicate_checker.set_required_link_count(
            maintaining_progress_bar.total,
        )
