from asyncio import Event
from datetime import datetime
from types import SimpleNamespace


class DuplicateLinkChecker:
    def __init__(self):
        self._all_links_received = Event()
        self._original_links = {}

    def add_link(self, raindrop):
        _id = raindrop["_id"]
        created = datetime.fromisoformat(raindrop["created"])
        url = raindrop["link"]
        original_link = self._original_links.get(url)

        if (
            original_link is None
            or created < original_link.created
            or (created == original_link.created and _id < original_link.id)
        ):
            self._original_links[url] = SimpleNamespace(
                created=created,
                id=_id,
            )

    async def is_link_duplicate(self, raindrop):
        if not self._all_links_received.is_set():
            await self._all_links_received.wait()

        created = datetime.fromisoformat(raindrop["created"])
        original_link = self._original_links[raindrop["link"]]

        return original_link.created < created or (
            original_link.created == created
            and original_link.id < raindrop["_id"]
        )

    def set_all_links_received(self):
        self._all_links_received.set()
