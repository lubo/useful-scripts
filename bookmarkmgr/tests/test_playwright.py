import re
from typing import TYPE_CHECKING

from playwright.async_api import async_playwright
import pytest
import pytest_asyncio

from bookmarkmgr.playwright import BrowserManager, RequestError, Session

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest_asyncio.fixture(scope="module")
async def client_session() -> AsyncIterator[Session]:
    async with (
        async_playwright() as playwright,
        BrowserManager(playwright) as browser_manager,
        Session(browser_manager.get_browser()) as session,
    ):
        yield session


@pytest.mark.asyncio(loop_scope="module")
async def test_http2_fingerprint(client_session: Session) -> None:
    response = await client_session.get(
        "https://tls.browserleaks.com/http2",
    )

    # Chrome 146
    expected = "52d84b11737d980aef856699f885ca86"
    actual = response.json().get("akamai_hash")

    assert actual == expected


@pytest.mark.asyncio(loop_scope="module")
async def test_quic_fingerprint(client_session: Session) -> None:
    # Necessary for the next request to be upgraded to QUIC.
    await client_session.get(
        "https://quic.browserleaks.com/",
    )

    response = await client_session.get(
        "https://quic.browserleaks.com/",
    )
    fingerprints = response.json()

    # Chrome 146
    expected = {
        "ja4": "q13d0311h3_55b375c5d22e_653d80c3fe9d",
        "h3_hash": "ba909fc3dc419ea5c5b26c6323ac1879",
    }
    actual = {key: fingerprints.get(key) for key in expected}

    assert actual == expected


@pytest.mark.asyncio(loop_scope="module")
async def test_tls_fingerprint(client_session: Session) -> None:
    response = await client_session.get(
        "https://tls.browserleaks.com/tls",
    )
    fingerprints = response.json()

    # Chrome 146
    expected = {
        "ja4": "t13d1516h2_8daaf6152771_d8a2da3f94cd",
        "ja3n_hash": "8e19337e7524d2573be54efb2b0784c9",
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
async def test_request_error(client_session: Session) -> None:
    url = "https://invalid/"

    with pytest.raises(
        RequestError,
        match=r"net::ERR_NAME_NOT_RESOLVED: GET " + re.escape(url),
    ):
        await client_session.get(url)


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.xfail(
    reason="requires setting HttpsOnlyMode=force_* Chrome policy",
)
async def test_https_is_always_used(client_session: Session) -> None:
    url = "http://portquiz.net/"

    with pytest.raises(
        RequestError,
        match=r"net::ERR_BLOCKED_BY_CLIENT: GET " + re.escape(url),
    ):
        await client_session.get(url)
