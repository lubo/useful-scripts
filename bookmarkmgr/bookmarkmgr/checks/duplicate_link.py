from asyncio import Event
from datetime import datetime


class _Link:
    def __init__(self, raindrop):
        self.created = datetime.fromisoformat(raindrop["created"])
        self.id = raindrop["_id"]

    def __lt__(self, other):
        return self.created < other.created or (
            self.created == other.created and self.id < other.id
        )


class DuplicateLinkChecker:
    def __init__(self):
        self._all_links_received = Event()
        self._original_links = {}

    def add_link(self, raindrop):
        url = raindrop["link"]
        original_link = self._original_links.get(url)
        tested_link = _Link(raindrop)

        if original_link is None or tested_link < original_link:
            self._original_links[url] = tested_link

    async def is_link_duplicate(self, raindrop):
        if not self._all_links_received.is_set():
            await self._all_links_received.wait()

        original_link = self._original_links[raindrop["link"]]
        tested_link = _Link(raindrop)

        return original_link < tested_link

    def set_all_links_received(self):
        self._all_links_received.set()
