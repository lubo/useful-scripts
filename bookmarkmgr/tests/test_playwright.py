import re
from typing import TYPE_CHECKING

from playwright.async_api import async_playwright
import pytest
import pytest_asyncio

from bookmarkmgr.playwright import BrowserManager, RequestError, Session

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest_asyncio.fixture(
    scope="module",
)
async def client_session() -> AsyncIterator[Session]:
    async with (
        async_playwright() as playwright,
        BrowserManager(playwright) as browser_manager,
        Session(browser_manager.get_browser()) as session,
    ):
        yield session


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.xfail(
    reason=(
        "requires setting HttpsOnlyMode=force_* Chrome policy prior to M154"
    ),
)
async def test_http_is_blocked(client_session: Session) -> None:
    url = "http://portquiz.net/"

    with pytest.raises(
        RequestError,
        match=r"net::ERR_BLOCKED_BY_CLIENT: GET " + re.escape(url),
    ):
        await client_session.get(url)


@pytest.mark.asyncio(loop_scope="module")
async def test_http2_fingerprint(client_session: Session) -> None:
    response = await client_session.get(
        "https://tls.browserleaks.com/http2",
        allow_redirects=False,
    )
    fingerprints: dict[str, object] = response.json()

    # Brave 1.97
    expected = {
        "akamai_hash": "52d84b11737d980aef856699f885ca86",
        "headers": {
            ":method": "GET",
            ":authority": "tls.browserleaks.com",
            ":scheme": "https",
            ":path": "/http2",
            "cache-control": "max-age=0",
            "sec-ch-ua": '"Google Chrome";v="155", "Chromium";v="155", "Not(A:Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
            "dnt": "1",
            "upgrade-insecure-requests": "1",
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36",
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/jxl,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "sec-fetch-site": "none",
            "sec-fetch-mode": "navigate",
            "sec-fetch-user": "?1",
            "sec-fetch-dest": "document",
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "en-SK,en;q=0.9,sk-SK;q=0.8,sk;q=0.7",
            "priority": "u=0, i",
        },
    }
    actual = {
        "akamai_hash": fingerprints.get("akamai_hash"),
        "headers": (fingerprints.get("http2", [])[2:3] or [{}])[0].get(
            "headers", {},
        ),
    }

    assert actual == expected


@pytest.mark.asyncio(loop_scope="module")
async def test_https_is_not_blocked(client_session: Session) -> None:
    url = "https://portquiz.net/"

    with pytest.raises(
        RequestError,
        match=r"net::ERR_SSL_PROTOCOL_ERROR: GET " + re.escape(url),
    ):
        await client_session.get(url)


@pytest.mark.asyncio(loop_scope="module")
async def test_quic_fingerprint(client_session: Session) -> None:
    # Necessary for the next request to be upgraded to QUIC.
    await client_session.get(
        "https://quic.browserleaks.com/",
        allow_redirects=False,
    )

    response = await client_session.get(
        "https://quic.browserleaks.com/",
        allow_redirects=False,
    )
    fingerprints: dict[str, object] = response.json()

    # Brave 1.97
    expected = {
        "ja4": "q13d0311h3_55b375c5d22e_653d80c3fe9d",
        "h3_hash": "ba909fc3dc419ea5c5b26c6323ac1879",
    }
    actual = {key: fingerprints.get(key) for key in expected}

    assert actual == expected


@pytest.mark.asyncio(loop_scope="module")
async def test_redirect_allowed(client_session: Session) -> None:
    response = await client_session.get(
        "https://httpbin.org/redirect/1",
    )

    expected = dict(  # noqa: C408
        status_code=200,
        redirect_url=None,
        url="https://httpbin.org/get",
    )
    actual = dict(  # noqa: C408
        status_code=response.status_code,
        redirect_url=response.redirect_url,
        url=response.url,
    )

    assert actual == expected


@pytest.mark.asyncio(loop_scope="module")
async def test_redirect_disallowed(client_session: Session) -> None:
    response = await client_session.get(
        "https://httpbin.org/redirect/1",
        allow_redirects=False,
    )

    expected = dict(  # noqa: C408
        status_code=302,
        redirect_url="https://httpbin.org/get",
        url="https://httpbin.org/redirect/1",
    )
    actual = dict(  # noqa: C408
        status_code=response.status_code,
        redirect_url=response.redirect_url,
        url=response.url,
    )

    assert actual == expected


@pytest.mark.asyncio(loop_scope="module")
async def test_redirect_url_is_final(client_session: Session) -> None:
    response = await client_session.get(
        "https://httpbin.org/redirect/2",
        allow_redirects=False,
    )

    actual = dict(  # noqa: C408
        redirect_url=response.redirect_url,
        url=response.url,
    )
    expected = dict(  # noqa: C408
        redirect_url="https://httpbin.org/get",
        url="https://httpbin.org/redirect/2",
    )

    assert actual == expected


@pytest.mark.asyncio(loop_scope="module")
async def test_request_error(client_session: Session) -> None:
    url = "https://invalid/"

    with pytest.raises(
        RequestError,
        match=r"net::ERR_NAME_NOT_RESOLVED: GET " + re.escape(url),
    ):
        await client_session.get(url)


@pytest.mark.asyncio(loop_scope="module")
async def test_tls_fingerprint(client_session: Session) -> None:
    response = await client_session.get(
        "https://tls.browserleaks.com/tls",
        allow_redirects=False,
    )
    fingerprints: dict[str, object] = response.json()

    # Brave 1.97
    expected = {
        "ja4": "t13d1516h2_8daaf6152771_806a8c22fdea",
        "ja3n_hash": "8e19337e7524d2573be54efb2b0784c9",
    }
    actual = {key: fingerprints.get(key) for key in expected}

    assert actual == expected
