from enum import IntEnum, unique
from html.parser import HTMLParser
import re
from urllib.parse import urlparse

from ..cronet import RequestError


PERMANENT_REDIRECT_STATUS_CODES = {
    301,
    308,
}

REDIRECT_STATUS_CODES = {
    *PERMANENT_REDIRECT_STATUS_CODES,
    302,
    307,
}

NOT_FOUND_STATUS_CODES = {
    404,
    410,
}

BROKEN_STATUS_CODES = {
    *PERMANENT_REDIRECT_STATUS_CODES,
    *NOT_FOUND_STATUS_CODES,
}

INVALID_HTML_PARENTS = {
    "base",
    "link",
    "meta",
}


@unique
class LinkStatus(IntEnum):
    OK = 1
    BROKEN = 2
    POSSIBLY_BROKEN = 3
    BLOCKED = 4


class _HTMLParser(HTMLParser):
    def reset(self, *args, **kwargs):
        self._path = []
        self.body_text = ""
        self.title = ""

        return super().reset(*args, **kwargs)

    def handle_starttag(self, tag, attrs):
        if tag not in INVALID_HTML_PARENTS:
            self._path.append(tag)

    def handle_endtag(self, tag):
        if len(self._path) > 0 and self._path[-1] == tag:
            self._path.pop()

    def handle_data(self, data):
        match self._path:
            case ["html", "body"]:
                self.body_text = data.strip()
            case ["html", "head", "title"]:
                self.title = data.strip()


async def check_is_link_broken(session, url):
    html_parser = None

    def retry_predicate(response):
        if response.status_code != 200:
            return False

        nonlocal html_parser

        if html_parser is None:
            html_parser = _HTMLParser()
        else:
            html_parser.reset()

        html_parser.feed(response.text)

        return html_parser.body_text == "Loading..."  # Rate limit hit

    link_status = LinkStatus.OK
    error = None
    fixed_url = None

    try:
        response = await session.get(
            url,
            allow_redirects=False,
            retry_predicate=retry_predicate,
        )
    except RequestError as error:
        link_status = LinkStatus.POSSIBLY_BROKEN
        error = str(error)

        return link_status, error, fixed_url

    match response.status_code:
        case 200:
            if (
                match := re.fullmatch(
                    r"(Post Not Found) \[[0-9a-f]+\] - [a-zA-Z]{8}",
                    html_parser.title,
                )
            ) is not None:
                link_status = LinkStatus.BROKEN
                error = match.group(1)
        case 401 | 403:
            link_status = LinkStatus.BLOCKED

    if error is not None or (
        response.ok and response.status_code not in REDIRECT_STATUS_CODES
    ):
        return link_status, error, fixed_url

    error = f"{response.status_code} {response.reason}"

    if link_status != LinkStatus.OK:
        return link_status, error, fixed_url

    if (
        response.status_code
        not in REDIRECT_STATUS_CODES | NOT_FOUND_STATUS_CODES
    ):
        link_status = LinkStatus.POSSIBLY_BROKEN

        return link_status, error, fixed_url

    # Raindrop breaks some links during import by removing trailing slash.
    parsed_url = urlparse(url)
    potentially_fixed_url = (
        parsed_url._replace(
            path=f"{parsed_url.path}/",
        ).geturl()
        if not parsed_url.path.endswith("/")
        else url
    )
    link_status = (
        LinkStatus.BROKEN
        if response.status_code in BROKEN_STATUS_CODES
        else LinkStatus.POSSIBLY_BROKEN
    )

    if (
        response.status_code in REDIRECT_STATUS_CODES
        and response.redirect_url != potentially_fixed_url
    ) or (
        response.status_code in NOT_FOUND_STATUS_CODES
        and url == potentially_fixed_url
    ):
        return link_status, error, fixed_url

    new_link_status, _, _ = await check_is_link_broken(
        session,
        potentially_fixed_url,
    )

    if new_link_status == LinkStatus.OK:
        fixed_url = potentially_fixed_url

    return link_status, error, fixed_url
