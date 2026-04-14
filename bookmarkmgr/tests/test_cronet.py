from http.cookiejar import Cookie
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from bookmarkmgr.cronet import Session

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest_asyncio.fixture(scope="session")
async def cronet_session() -> AsyncIterator[Session]:
    session = Session()

    for name, value in [("foo", "bar"), ("bar", "foo")]:
        session.cookie_jar.set_cookie(
            Cookie(
                version=0,
                name=name,
                value=value,
                port=None,
                port_specified=False,
                domain="browserleaks.com",
                domain_specified=True,
                domain_initial_dot=True,
                path="",
                path_specified=False,
                secure=False,
                expires=None,
                discard=False,
                comment=None,
                comment_url=None,
                rest={},
            ),
        )

    async with session:
        yield session


@pytest.mark.asyncio
async def test_http2_fingerprint(cronet_session: Session) -> None:
    response = await cronet_session.get(
        "https://tls.browserleaks.com/http2",
        allow_redirects=False,
    )

    # Chrome 146
    expected = "52d84b11737d980aef856699f885ca86"
    actual = response.json().get("akamai_hash")

    assert actual == expected


@pytest.mark.asyncio
async def test_quic_fingerprint(cronet_session: Session) -> None:
    # Necessary for the next request to be upgraded to QUIC.
    await cronet_session.get(
        "https://quic.browserleaks.com/",
        allow_redirects=False,
    )

    response = await cronet_session.get(
        "https://quic.browserleaks.com/",
        allow_redirects=False,
    )
    fingerprints = response.json()

    # Chrome 146
    expected = {
        "ja4": "q13d0311h3_55b375c5d22e_653d80c3fe9d",
        "h3_hash": "ba909fc3dc419ea5c5b26c6323ac1879",
    }
    actual = {key: fingerprints.get(key) for key in expected}

    assert actual == expected


@pytest.mark.asyncio
async def test_tls_fingerprint(cronet_session: Session) -> None:
    response = await cronet_session.get(
        "https://tls.browserleaks.com/tls",
        allow_redirects=False,
    )
    fingerprints = response.json()

    # Chrome 146
    expected = {
        "ja4": "t13d1516h2_8daaf6152771_d8a2da3f94cd",
        "ja3n_hash": "8e19337e7524d2573be54efb2b0784c9",
    }
    actual = {key: fingerprints.get(key) for key in expected}

    assert actual == expected
