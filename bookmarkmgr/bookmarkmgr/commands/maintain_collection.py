import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import enlighten

from bookmarkmgr.asyncio import ForgivingTaskGroup
from bookmarkmgr.checks.broken_link import check_is_link_broken, LinkStatus
from bookmarkmgr.checks.duplicate_link import DuplicateLinkChecker
from bookmarkmgr.clients.archive_today import ArchiveTodayClient
from bookmarkmgr.clients.wayback_machine import WaybackMachineClient
from bookmarkmgr.cronet import PerHostnameRateLimitedSession
from bookmarkmgr.logging import get_logger
from bookmarkmgr.utils.link_metadata import (
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


@asynccontextmanager
async def get_progress_bar(manager, description, **kwargs):
    progress_bar = manager.counter(
        desc=description,
        unit="links",
        **kwargs,
    )
    stop_refreshing = False

    async def refresh():
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


class DefaultsDict(dict):
    def __init__(self, *args, defaults, **kwargs):
        super().__init__(*args, **kwargs)

        self._defaults = defaults

    def __missing__(self, key):
        return self._defaults[key]


async def process_archival_result(
    result_future,
    raindrop,
    metadata,
    service_initials,
    service_name,
):
    archival_url, error = await result_future

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


def add_or_remove_tag(raindrop, add_tag, tag, error=None):
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


async def process_check_broken_result(
    result_future,
    raindrop,
    metadata,
    today,
):
    link_status, error, fixed_url = await result_future
    metadata["Last check"] = str(today)

    if link_status in BROKEN_LINK_STATUSES:
        if fixed_url is None:
            try:
                broken_since = datetime.fromisoformat(
                    metadata.get("Broken since"),
                ).replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                broken_since = today
                metadata["Broken since"] = str(broken_since)

            if link_status == LinkStatus.POSSIBLY_BROKEN and (
                today >= broken_since + timedelta(days=7)
            ):
                link_status = LinkStatus.BROKEN
        else:
            logger.info("Fixing URL to %s", fixed_url)

            link_status = LinkStatus.OK
            raindrop["link"] = fixed_url

    if link_status not in BROKEN_LINK_STATUSES and "Broken since" in metadata:
        del metadata["Broken since"]

    for status, tag in LINK_STATUS_TAGS.items():
        add_or_remove_tag(
            raindrop,
            link_status == status,
            tag,
            error,
        )


async def process_check_duplicate_result(result_future, raindrop):
    add_or_remove_tag(
        raindrop,
        await result_future,
        "duplicate",
    )


async def maintain_raindrop(  # noqa: C901, PLR0913
    raindrop_client,
    raindrop,
    at_client,
    wm_client,
    check_session,
    duplicate_checker,
    no_archive,
    no_archive_broken,
    no_checks,
):
    is_link_broken = (
        len(
            {"broken", "possibly-broken"}.intersection(raindrop["tags"]),
        )
        > 0
    )
    link = raindrop["link"]
    note_metadata = metadata_from_note(raindrop["note"])
    task_group_error = None
    today = datetime.now(tz=timezone.utc)
    updated_raindrop = DefaultsDict(defaults=raindrop)

    try:
        last_check = datetime.fromisoformat(
            note_metadata.get("Last check"),
        ).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        last_check = datetime.fromtimestamp(0, tz=timezone.utc)

    duplicate_checker.add_link(raindrop)

    try:
        async with ForgivingTaskGroup() as task_group:
            if not (
                no_archive
                or (no_archive_broken and is_link_broken)
                or note_metadata.get("Archive (AT)")
                or note_metadata.get("Archival Error (AT)")
            ):
                task_group.create_task(
                    process_archival_result(
                        at_client.archive_page(link),
                        updated_raindrop,
                        note_metadata,
                        "AT",
                        "archive.today",
                    ),
                    name=f"Archive-Today-{link}",
                )

            if not (
                no_archive
                or (no_archive_broken and is_link_broken)
                or note_metadata.get("Archive (WM)")
                or note_metadata.get("Archival Error (WM)")
            ):
                task_group.create_task(
                    process_archival_result(
                        wm_client.archive_page(link),
                        updated_raindrop,
                        note_metadata,
                        "WM",
                        "Wayback Machine",
                    ),
                    name=f"Wayback-Machine-{link}",
                )

            if not (
                no_checks
                or "broken" in raindrop["tags"]
                or (
                    "possibly-broken" not in raindrop["tags"]
                    and today < last_check + timedelta(days=1)
                )
            ):
                task_group.create_task(
                    process_check_broken_result(
                        check_is_link_broken(
                            check_session,
                            link,
                        ),
                        updated_raindrop,
                        note_metadata,
                        today,
                    ),
                    name=f"Check-If-Broken-{link}",
                )

            if not no_checks:
                task_group.create_task(
                    process_check_duplicate_result(
                        duplicate_checker.is_link_duplicate(raindrop),
                        updated_raindrop,
                    ),
                    name=f"Check-Is-Duplicate-{link}",
                )
    except ExceptionGroup as error:
        task_group_error = error

    try:
        if raindrop["note"] != (new_note := metadata_to_note(note_metadata)):
            updated_raindrop["note"] = new_note

        if updated_raindrop["tags"] != (
            sorted_tags := sorted(updated_raindrop["tags"])
        ):
            updated_raindrop["tags"] = sorted_tags

        if not updated_raindrop:
            return

        await raindrop_client.update_raindrop(
            raindrop["_id"],
            updated_raindrop,
        )
    finally:
        if task_group_error is not None:
            raise task_group_error


async def maintain_collection(  # noqa: PLR0913
    raindrop_client,
    collection_id,
    host_rate_limits,
    no_archive,
    no_archive_broken,
    no_checks,
):
    items = raindrop_client.get_collection_items(collection_id)
    duplicate_checker = DuplicateLinkChecker()
    progress_bar_manager = enlighten.get_manager()

    async with (
        ArchiveTodayClient() as at_client,
        WaybackMachineClient() as wm_client,
        PerHostnameRateLimitedSession(
            host_rate_limits=host_rate_limits,
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
            task,  # noqa: ARG001
        ):
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
                    no_archive,
                    no_archive_broken,
                    no_checks,
                ),
                name=f"Maintain-{item['link']}",
            )
            task.add_done_callback(on_task_done)

            await asyncio.sleep(0)

        maintaining_progress_bar.total = loading_progress_bar.count

        duplicate_checker.set_required_link_count(
            maintaining_progress_bar.total,
        )
