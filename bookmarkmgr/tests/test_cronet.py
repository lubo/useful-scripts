from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from bookmarkmgr.cronet import Session


@pytest_asyncio.fixture(scope="session")
async def cronet_session() -> AsyncIterator[Session]:
    async with Session() as session:
        yield session


@pytest.mark.asyncio
async def test_http2_fingerprint(cronet_session: Session) -> None:
    response = await cronet_session.get("https://tls.browserleaks.com/http2")

    # Chrome 146
    expected = "52d84b11737d980aef856699f885ca86"
    actual = response.json().get("akamai_hash")

    assert expected == actual


@pytest.mark.asyncio
async def test_quic_fingerprint(cronet_session: Session) -> None:
    # This requests makes sure that the next one is upgraded to QUIC.
    await cronet_session.get("https://quic.browserleaks.com/")

    response = await cronet_session.get("https://quic.browserleaks.com/")
    fingerprints = response.json()

    # Chrome 146
    expected = {
        "ja4": "q13d0311h3_55b375c5d22e_653d80c3fe9d",
        "h3_hash": "ba909fc3dc419ea5c5b26c6323ac1879",
    }
    actual = {key: fingerprints.get(key) for key in expected}

    assert expected == actual


@pytest.mark.asyncio
async def test_tls_fingerprint(cronet_session: Session) -> None:
    response = await cronet_session.get("https://tls.browserleaks.com/tls")
    fingerprints = response.json()

    # Chrome 146
    expected = {
        "ja4": "t13d1516h2_8daaf6152771_d8a2da3f94cd",
        "ja3n_hash": "8e19337e7524d2573be54efb2b0784c9",
    }
    actual = {key: fingerprints.get(key) for key in expected}

    assert expected == actual
