from asyncio import Event
from datetime import datetime
from urllib.parse import urlparse


class _Link:
    def __init__(self, raindrop):
        self.created = datetime.fromisoformat(raindrop["created"])
        self.id = raindrop["_id"]

    def __lt__(self, other):
        return self.created < other.created or (
            self.created == other.created and self.id < other.id
        )


def _remove_query_from_url(url):
    parsed_url = urlparse(url)

    if parsed_url.query == "":
        return url

    return parsed_url._replace(query="").geturl()


class DuplicateLinkChecker:
    def __init__(self):
        self._all_links_received = Event()
        self._link_count = 0
        self._original_links = {}
        self._required_link_count = 0

    def add_link(self, raindrop):
        url = raindrop["link"]
        original_link = self._original_links.get(url)
        tested_link = _Link(raindrop)

        if original_link is None or tested_link < original_link:
            self._original_links[url] = tested_link

        self._link_count += 1
        self._process_links()

    async def is_link_duplicate(self, raindrop):
        if not self._all_links_received.is_set():
            await self._all_links_received.wait()

        url = raindrop["link"]
        queryless_url = _remove_query_from_url(url)

        if queryless_url != url and queryless_url in self._original_links:
            url = queryless_url

        original_link = self._original_links[url]
        tested_link = _Link(raindrop)

        return original_link < tested_link

    def set_required_link_count(self, count):
        self._required_link_count = count
        self._process_links()

    def _process_links(self):
        if self._link_count != self._required_link_count:
            return

        for url, link in self._original_links.items():
            queryless_url = _remove_query_from_url(url)

            if (
                queryless_url == url
                or queryless_url not in self._original_links
            ):
                continue

            if link < self._original_links[queryless_url]:
                self._original_links[queryless_url] = link

        self._all_links_received.set()
