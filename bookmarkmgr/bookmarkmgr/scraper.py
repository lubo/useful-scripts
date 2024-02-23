import asyncio
from dataclasses import dataclass
from html.parser import HTMLParser
from http import HTTPStatus

from bookmarkmgr.cronet import Response, Session

INVALID_HTML_PARENTS = {
    "base",
    "link",
    "meta",
}


@dataclass(slots=True)
class Page:
    body_text: str = ""
    canonical_url: str | None = None
    default_lang_url: str | None = None
    og_url: str | None = None
    title: str = ""


def _scrape_html(html: str) -> Page:  # noqa: C901
    page = Page()
    path: list[str] = []

    def handle_selfclosingtag(
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if path != ["html", "head"]:
            return

        attrs_dict = {key: value for key, value in attrs}  # noqa: C416

        match tag:
            case "link":
                match attrs_dict.get("rel"):
                    case "alternate":
                        match attrs_dict.get("hreflang"):
                            case "x-default":
                                page.default_lang_url = attrs_dict.get("href")
                    case "canonical":
                        page.canonical_url = attrs_dict.get("href")
            case "meta":
                match attrs_dict.get("property"):
                    case "og:url":
                        page.og_url = attrs_dict.get("content")

    def handle_data(data: str) -> None:
        match path:
            case ["html", "body"]:
                page.body_text = data.strip()
            case ["html", "head", "title"]:
                page.title = data.strip()

    def handle_endtag(tag: str) -> None:
        if len(path) > 0 and path[-1] == tag:
            del path[-1]

    def handle_startendtag(
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        handle_selfclosingtag(tag, attrs)

    def handle_starttag(
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        handle_selfclosingtag(tag, attrs)

        if tag not in INVALID_HTML_PARENTS:
            path.append(tag)

    html_parser = HTMLParser()
    html_parser.handle_data = handle_data  # type: ignore[method-assign]
    html_parser.handle_endtag = handle_endtag  # type: ignore[method-assign]
    html_parser.handle_startendtag = (  # type: ignore[method-assign]
        handle_startendtag
    )
    html_parser.handle_starttag = (  # type: ignore[method-assign]
        handle_starttag
    )

    html_parser.feed(html)

    return page


async def get_page(
    session: Session,
    url: str,
) -> tuple[Page | None, Response]:
    page = None

    async def retry_predicate(response: Response) -> bool:
        if response.status_code != HTTPStatus.OK.value:
            return False

        nonlocal page

        page = await asyncio.to_thread(_scrape_html, response.text)

        return page.body_text == "Loading..."  # Rate limit hit

    response = await session.get(
        url,
        allow_redirects=False,
        retry_predicate=retry_predicate,
    )

    return page, response
