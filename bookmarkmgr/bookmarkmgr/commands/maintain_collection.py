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

logger = get_logger()

LINK_STATUS_TAGS = {
    LinkStatus.BROKEN: "broken",
    LinkStatus.POSSIBLY_BROKEN: "possibly-broken",
    LinkStatus.BLOCKED: "blocked",
}


@asynccontextmanager
async def get_progress_bar():
    progress_bar = enlighten.get_manager().counter(
        desc="Maintaining",
        unit="links",
    )
    stop_refreshing = False

    async def refresh():
        while True:
            progress_bar.refresh()

            if stop_refreshing:
                break

            await asyncio.sleep(1)

    refresh_task = asyncio.create_task(refresh())

    yield progress_bar

    stop_refreshing = True
    await refresh_task


class DefaultsDict(dict):
    def __init__(self, *args, defaults, **kwargs):
        super().__init__(*args, **kwargs)

        self._defaults = defaults

    def __missing__(self, key):
        return self._defaults[key]


def metadata_to_note(metadata):
    return "\n".join(
        [f"{key}: {value}" for key, value in sorted(metadata.items())],
    )


def note_to_metadata(note):
    metadata = {}

    for line in note.splitlines():
        segments = line.split(":", 1)

        if len(segments) == 1:
            segments.append("")

        metadata[segments[0]] = segments[1].strip()

    return metadata


def process_archival_result(
    task,
    raindrop,
    metadata,
    service_initials,
    service_name,
):
    if task is None or task.exception() is not None:
        return

    archival_url, error = task.result()

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


def process_check_broken_result(task, raindrop, metadata, today):
    if task is None or task.exception() is not None:
        return

    link_status, error, fixed_url = task.result()
    metadata["Last check"] = str(today)

    if link_status not in {LinkStatus.BROKEN, LinkStatus.POSSIBLY_BROKEN}:
        if "Broken since" in metadata:
            del metadata["Broken since"]
    elif fixed_url is None:
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

    for status, tag in LINK_STATUS_TAGS.items():
        add_or_remove_tag(
            raindrop,
            link_status == status,
            tag,
            error,
        )


def process_check_duplicate_result(task, raindrop):
    if task is None or task.exception() is not None:
        return

    add_or_remove_tag(
        raindrop,
        task.result(),
        "duplicate",
    )


async def maintain_raindrop(  # noqa: PLR0913
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
    note_metadata = note_to_metadata(raindrop["note"])
    task_group_error = None
    today = datetime.now(tz=timezone.utc)

    try:
        last_check = datetime.fromisoformat(
            note_metadata.get("Last check"),
        ).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        last_check = datetime.fromtimestamp(0, tz=timezone.utc)

    try:
        async with ForgivingTaskGroup() as task_group:
            at_archival_task = (
                None
                if no_archive
                or (no_archive_broken and is_link_broken)
                or note_metadata.get("Archive (AT)")
                or note_metadata.get("Archival Error (AT)")
                else task_group.create_task(
                    at_client.archive_page(link),
                    name=f"Archive-Today-Archive-{link}",
                )
            )

            wm_archival_task = (
                None
                if no_archive
                or (no_archive_broken and is_link_broken)
                or note_metadata.get("Archive (WM)")
                or note_metadata.get("Archival Error (WM)")
                else task_group.create_task(
                    wm_client.archive_page(link),
                    name=f"Wayback-Machine-Archive-{link}",
                )
            )

            check_broken_task = (
                None
                if no_checks
                or "broken" in raindrop["tags"]
                or (
                    "possibly-broken" not in raindrop["tags"]
                    and today < last_check + timedelta(days=1)
                )
                else task_group.create_task(
                    check_is_link_broken(
                        check_session,
                        link,
                    ),
                    name=f"Check-If-Broken-{link}",
                )
            )

            check_duplicate_task = (
                None
                if no_checks
                else task_group.create_task(
                    duplicate_checker.is_link_duplicate(raindrop),
                    name=f"Check-Is-Duplicate-{link}",
                )
            )
    except ExceptionGroup as error:
        task_group_error = error

    try:
        updated_raindrop = DefaultsDict(defaults=raindrop)

        process_archival_result(
            at_archival_task,
            updated_raindrop,
            note_metadata,
            "AT",
            "archive.today",
        )

        process_archival_result(
            wm_archival_task,
            updated_raindrop,
            note_metadata,
            "WM",
            "Wayback Machine",
        )

        process_check_broken_result(
            check_broken_task,
            updated_raindrop,
            note_metadata,
            today,
        )

        process_check_duplicate_result(
            check_duplicate_task,
            updated_raindrop,
        )

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

    async with (
        ArchiveTodayClient() as at_client,
        WaybackMachineClient() as wm_client,
        PerHostnameRateLimitedSession(
            host_rate_limits=host_rate_limits,
        ) as check_session,
        get_progress_bar() as progress_bar,
        ForgivingTaskGroup() as task_group,
    ):

        def on_task_done(
            task,  # noqa: ARG001
        ):
            progress_bar.count += 1

        count = 0

        async for item in items:
            count += 1

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

            duplicate_checker.add_link(item)

        progress_bar.total = count

        duplicate_checker.set_all_links_received()
