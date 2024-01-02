from asyncio import Event
from datetime import datetime


class _Link:
    def __init__(self, raindrop):
        self.created = datetime.fromisoformat(raindrop["created"])
        self.id = raindrop["_id"]


def _is_first_link_older(link1, link2):
    return link1.created < link2.created or (
        link1.created == link2.created and link1.id < link2.id
    )


class DuplicateLinkChecker:
    def __init__(self):
        self._all_links_received = Event()
        self._original_links = {}

    def add_link(self, raindrop):
        url = raindrop["link"]
        original_link = self._original_links.get(url)
        tested_link = _Link(raindrop)

        if original_link is None or _is_first_link_older(
            tested_link,
            original_link,
        ):
            self._original_links[url] = tested_link

    async def is_link_duplicate(self, raindrop):
        if not self._all_links_received.is_set():
            await self._all_links_received.wait()

        original_link = self._original_links[raindrop["link"]]
        tested_link = _Link(raindrop)

        return _is_first_link_older(original_link, tested_link)

    def set_all_links_received(self):
        self._all_links_received.set()
