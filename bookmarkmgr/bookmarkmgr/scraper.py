from dataclasses import dataclass
import gc
from html.parser import HTMLParser
from http import HTTPStatus

from bookmarkmgr import asyncio, cronet
from bookmarkmgr.cronet import RequestError, ResponseStatus, Session

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


@dataclass(slots=True)
class Response(ResponseStatus):
    reason: str
    redirect_url: str | None = None


@dataclass(slots=True)
class ScrapedData:
    response: Response
    page: Page | None = None


type Result = RequestError | ScrapedData


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


async def scrape_page(
    session: Session,
    url: str,
) -> Result:
    page = None

    async def retry_predicate(response: cronet.Response) -> bool:
        if response.status_code != HTTPStatus.OK.value:
            return False

        nonlocal page

        page = await asyncio.to_cpu_bound_giled_thread(
            _scrape_html,
            response.text,
        )

        return page.body_text == "Loading..."  # Rate limit hit

    try:
        response = await session.get(
            url,
            allow_redirects=False,
            retry_predicate=retry_predicate,
        )
    except RequestError as error:
        return error

    result = ScrapedData(
        page=page,
        response=Response(
            reason=response.reason,
            redirect_url=response.redirect_url,
            status_code=response.status_code,
        ),
    )

    # Response.content is ~2.4 MiB on average, so a massive amount of memory
    # may be used unless we free it ASAP.
    del response
    gc.collect()

    return result
