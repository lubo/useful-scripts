from enum import IntEnum, unique
from html.parser import HTMLParser
import re
from urllib.parse import urlparse

from curl_cffi.requests import RequestsError


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


@unique
class LinkStatus(IntEnum):
    OK = 1
    BROKEN = 2
    POSSIBLY_BROKEN = 3


class TitleTagFound(Exception):
    pass


class TitleHTMLParser(HTMLParser):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.stack = []
        self.title = ""

    def feed(self, *args, **kwargs):
        try:
            super().feed(*args, **kwargs)
        except TitleTagFound:
            pass

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)

    def handle_endtag(self, tag):
        self.stack.pop()

    def handle_data(self, data):
        if self.stack != ["html", "head", "title"]:
            return

        self.title = data.strip()

        raise TitleTagFound()


async def check_is_link_broken(session, url):
    link_status = LinkStatus.OK
    error = None
    fixed_url = None

    try:
        response = await session.get(url, allow_redirects=False)
    except RequestsError as error:
        link_status = LinkStatus.POSSIBLY_BROKEN
        error = str(error)

        return link_status, error, fixed_url

    if response.status_code == 200:
        title_parser = TitleHTMLParser()
        # TODO: Switch to acontent() on >=v0.5.10
        title_parser.feed(response.content.decode(response.charset, "replace"))

        if not title_parser.title:  # May be rate limit response
            link_status = LinkStatus.POSSIBLY_BROKEN
            error = "Missing title"
        elif (
            match := re.fullmatch(
                r"(Post Not Found) \[[0-9a-f]+\] - [a-zA-Z]{8}",
                title_parser.title,
            )
        ) is not None:
            link_status = LinkStatus.BROKEN
            error = match.group(1)

        if error is not None:
            return link_status, error, fixed_url

    if response.ok and response.status_code not in REDIRECT_STATUS_CODES:
        return link_status, error, fixed_url

    error = f"{response.status_code} {response.reason}"

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
