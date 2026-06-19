from dataclasses import dataclass
import gc
from html.parser import HTMLParser
from http import HTTPStatus
from typing import override

from yarl import URL

from bookmarkmgr import asyncio, cronet
from bookmarkmgr.cronet import RequestError, ResponseStatus, RetrySession

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
    og_image: str | None = None
    og_url: str | None = None
    title: str = ""


@dataclass(slots=True)
class Response(ResponseStatus):
    reason: str
    redirect_url: str | None = None


@dataclass(frozen=True, slots=True)
class ScrapedData:
    response: Response
    page: Page | None = None


type Result = RequestError | ScrapedData


class _HtmlParser(HTMLParser):
    __page: Page
    __path: list[str]

    def handle_selfclosingtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if self.__path != ["html", "head"]:
            return

        attrs_dict = {key: value for key, value in attrs}  # noqa: C416

        match tag:
            case "link":
                match attrs_dict.get("rel"):
                    case "alternate":
                        match attrs_dict.get("hreflang"):
                            case "x-default":
                                self.__page.default_lang_url = attrs_dict.get(
                                    "href",
                                )
                            case _:
                                pass
                    case "canonical":
                        self.__page.canonical_url = attrs_dict.get("href")
                    case _:
                        pass
            case "meta":
                match attrs_dict.get("property"):
                    case "og:image":
                        self.__page.og_image = attrs_dict.get("content")
                    case "og:url":
                        self.__page.og_url = attrs_dict.get("content")
                    case _:
                        pass
            case _:
                pass

    @override
    def handle_data(self, data: str) -> None:
        match self.__path:
            case ["html", "body"]:
                self.__page.body_text = data.strip()
            case ["html", "head", "title"]:
                self.__page.title = data.strip()
            case _:
                pass

    @override
    def handle_endtag(self, tag: str) -> None:
        if len(self.__path) > 0 and self.__path[-1] == tag:
            del self.__path[-1]

    @override
    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_selfclosingtag(tag, attrs)

    @override
    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_selfclosingtag(tag, attrs)

        if tag not in INVALID_HTML_PARENTS:
            self.__path.append(tag)

    @property
    def page(self) -> Page:
        return self.__page

    @override
    def reset(self) -> None:
        super().reset()

        self.__page = Page()
        self.__path = []


def _scrape_html(html: str) -> Page:
    html_parser = _HtmlParser()
    html_parser.feed(html)

    return html_parser.page


async def scrape_page(
    session: RetrySession,
    url: str,
) -> Result:
    parsed_url = URL(url)

    match parsed_url.scheme:
        case "" | "http":
            parsed_url = parsed_url.with_scheme("https")
        case "https":
            pass
        case _:
            message = f"Unsupported URL scheme: {parsed_url.scheme}"
            raise ValueError(message)

    page = None

    async def retry_predicate(response: cronet.Response) -> bool:
        nonlocal page

        page = None

        if response.status_code != HTTPStatus.OK.value:
            return False

        page = await asyncio.to_cpu_bound_giled_thread(
            _scrape_html,
            response.text,
        )

        return page.body_text == "Loading..."  # Rate limit hit

    try:
        response = await session.get(
            parsed_url,
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
