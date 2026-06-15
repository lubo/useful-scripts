from io import BytesIO

import pytest

from bookmarkmgr.clients.imgpile import ApiKey, Client, ErrorErrorResponse


@pytest.mark.asyncio
async def test_upload_is_unauthorized() -> None:
    client = Client(ApiKey("invalid"))
    result = await client.upload(BytesIO(b""))

    actual = result.value
    expected: ErrorErrorResponse = {
        "error": {
            "code": "unauthorized",
            "message": "Invalid or expired API key",
        },
    }

    assert actual == expected
