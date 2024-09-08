import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
import contextlib
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
from functools import partial
from typing import Any

import enlighten

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
from bookmarkmgr.utils.link_metadata import (
    Metadata,
    metadata_from_note,
    metadata_to_note,
)

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
    host_rate_limits: list[tuple[str, int, float]]
    no_archive: bool
    no_archive_broken: bool
    no_checks: bool


@asynccontextmanager
async def get_progress_bar(
    manager: enlighten.Manager,
    description: str,
    **kwargs: Any,
) -> AsyncIterator[enlighten.Counter]:
    progress_bar = manager.counter(
        desc=description,
        unit="links",
        **kwargs,
    )
    stop_refreshing = False

    async def refresh() -> None:
        while True:
            if stop_refreshing:
                progress_bar.close()
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
    result_awaitable: Awaitable[tuple[str | None, str | None]],
    raindrop: RaindropIn,
    metadata: Metadata,
    service_initials: str,
    service_name: str,
) -> None:
    try:
        archival_url, error = await result_awaitable
    except (ArchiveTodayError, WaybackMachineError) as e:
        logger.error("Archival of %s failed: %s", raindrop["link"], e)
        return

    if error is None:
        metadata[f"Archive ({service_initials})"] = f"[link]({archival_url})"
        tag = "archived"
    else:
        metadata[f"Archival Error ({service_initials})"] = error
        tag = "archival-failed"

        logger.error(
            "Archival in %s failed: %s: %s",
            service_name,
            error,
            raindrop["link"],
        )

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
    session: cronet.Session,
    url: str,
) -> tuple[scraper.Page | None, LinkStatus, str | None, str | None]:
    page = None
    response = None

    async def scrape(url: str) -> tuple[scraper.Page | None, cronet.Response]:
        nonlocal page, response

        page, response = await scraper.get_page(session, url)

        return page, response

    link_status, error = await check_link_status(scrape(url))

    if response is None or (fixed_url := get_fixed_url(response, url)) is None:
        return page, link_status, error, None

    old_page = page

    fixed_link_status, fixed_error = await check_link_status(
        scrape(fixed_url),
    )

    if fixed_link_status == LinkStatus.OK:
        return page, fixed_link_status, fixed_error, fixed_url

    return old_page, link_status, error, None


async def process_scrape_and_check_result(  # noqa: C901, PLR0912
    result_awaitable: Awaitable[
        tuple[scraper.Page | None, LinkStatus, str | None, str | None]
    ],
    raindrop: RaindropIn,
    metadata: Metadata,
    archival_tasks: list[asyncio.Task[Any]],
    create_archival_tasks: Callable[[asyncio.TaskGroup], Any],
) -> None:
    page, link_status, error, fixed_url = await result_awaitable
    now = datetime.now(tz=UTC)
    url = raindrop["link"]

    metadata["Last check"] = str(now)

    if fixed_url is not None:
        logger.info("Fixing URL to %s", fixed_url)

        raindrop["link"] = url = fixed_url

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

    if fixed_url is None:
        return

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

    async with ForgivingTaskGroup() as task_group:
        create_archival_tasks(task_group)


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
    wm_client: WaybackMachineClient,
    check_session: cronet.Session,
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

    if not (
        user_options.no_checks
        or "broken" in raindrop["tags"]
        or (
            "possibly-broken" not in raindrop["tags"]
            and datetime.now(tz=UTC) < last_check + timedelta(days=1)
        )
    ):
        task_group.create_task(
            process_scrape_and_check_result(
                scrape_and_check(check_session, link),
                raindrop,
                note_metadata,
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

        task_group.create_task(
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
    wm_client: WaybackMachineClient,
    check_session: cronet.Session,
    duplicate_checker: DuplicateLinkChecker,
    user_options: MaintainCollectionOptions,
) -> None:
    note_metadata = metadata_from_note(raindrop["note"])
    task_group_error = None
    updated_raindrop_with_defauls = TypedDefaultsDict[RaindropIn, RaindropOut](
        defaults=raindrop,
    )

    try:
        async with ForgivingTaskGroup() as task_group:
            create_raindrop_maintenance_tasks(
                task_group,
                updated_raindrop_with_defauls,
                note_metadata,
                at_client,
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
    user_options: MaintainCollectionOptions,
) -> None:
    items = raindrop_client.get_collection_items(collection_id)

    duplicate_checker = DuplicateLinkChecker()
    progress_bar_manager = enlighten.get_manager()

    async with (
        ArchiveTodayClient() as at_client,
        WaybackMachineClient() as wm_client,
        PerHostnameRateLimitedSession(
            host_rate_limits=user_options.host_rate_limits,
        ) as check_session,
        get_progress_bar(
            progress_bar_manager,
            "Maintaining",
        ) as maintaining_progress_bar,
        ForgivingTaskGroup() as task_group,
        get_progress_bar(
            progress_bar_manager,
            "  Loading",
            leave=False,
        ) as loading_progress_bar,
    ):

        def on_task_done(
            task: asyncio.Task[Any],  # noqa: ARG001
        ) -> None:
            maintaining_progress_bar.count += 1

        async for item in items:
            loading_progress_bar.count += 1

            task = task_group.create_task(
                maintain_raindrop(
                    raindrop_client,
                    item,
                    at_client,
                    wm_client,
                    check_session,
                    duplicate_checker,
                    user_options,
                ),
                name=f"Maintain-{item['link']}",
            )
            task.add_done_callback(on_task_done)

            await asyncio.sleep(0)

        maintaining_progress_bar.total = loading_progress_bar.count

        duplicate_checker.set_required_link_count(
            maintaining_progress_bar.total,
        )
