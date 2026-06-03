from enum import IntEnum, unique
from http import HTTPStatus
from typing import cast, TypedDict

from bookmarkmgr.aiohttp import RateLimitedRetryClientSession
from bookmarkmgr.asyncio import RateLimiter
from bookmarkmgr.clients import ClientSessionContextManagerMixin
from bookmarkmgr.types import Failure, Result, Success


class _BaseResponse(TypedDict):
    status_code: int


class ErrorResponseError(TypedDict):
    code: int
    message: str


class ErrorResponse(_BaseResponse):
    error: ErrorResponseError


class _SuccessResponseData(TypedDict):
    url: str


class SuccessResponse(_BaseResponse):
    data: _SuccessResponseData
    success: bool


type UploadResult = Result[SuccessResponse, ErrorResponse]


@unique
class ErrorCodes(IntEnum):
    RATE_LIMIT_REACHED = 100  # Rotate API key.
    FORBIDDEN = 103
    CANT_DOWNLOAD_REMOTE_IMAGE = 105
    INVALID_BASE64_STRING_OR_URL = 120
    UNSUPPORTED_OR_UNRECOGNIZED_FILE_FORMAT = 415


class Client(
    ClientSessionContextManagerMixin[RateLimitedRetryClientSession],
):
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._session = RateLimitedRetryClientSession(
            base_url="https://api.imgbb.com",
            rate_limiter=RateLimiter(
                limit=1,
                period=0,
            ),
        )

    async def upload_url(self, url: str) -> UploadResult:
        async with await self._session.post(
            "/1/upload",
            raise_for_status=False,
            params={
                "image": url,
                "key": self._api_key,
            },
        ) as response:
            data = await response.json()  # type: ignore[misc]

        if response.status == HTTPStatus.OK:
            return Success(cast("SuccessResponse", data))

        return Failure(cast("ErrorResponse", data))
