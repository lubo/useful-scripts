import asyncio
from html.parser import HTMLParser
from http import HTTPStatus

from bookmarkmgr.cronet import Response, Session

INVALID_HTML_PARENTS = {
    "base",
    "link",
    "meta",
}


class HTMLScraper(HTMLParser):
    def reset(self, *args, **kwargs):
        self._path = []
        self.body_text = ""
        self.title = ""

        return super().reset(*args, **kwargs)

    def handle_data(self, data):
        match self._path:
            case ["html", "body"]:
                self.body_text = data.strip()
            case ["html", "head", "title"]:
                self.title = data.strip()

    def handle_endtag(self, tag):
        if len(self._path) > 0 and self._path[-1] == tag:
            del self._path[-1]

    def handle_starttag(
        self,
        tag,
        attrs,  # noqa: ARG002
    ):
        if tag not in INVALID_HTML_PARENTS:
            self._path.append(tag)


async def get_page(
    session: Session,
    url: str,
) -> tuple[HTMLScraper | None, Response]:
    html_parser = None

    async def retry_predicate(response):
        if response.status_code != HTTPStatus.OK.value:
            return False

        nonlocal html_parser

        if html_parser is None:
            html_parser = HTMLScraper()
        else:
            html_parser.reset()

        await asyncio.to_thread(html_parser.feed, response.text)

        return html_parser.body_text == "Loading..."  # Rate limit hit

    response = await session.get(
        url,
        allow_redirects=False,
        retry_predicate=retry_predicate,
    )

    return html_parser, response
