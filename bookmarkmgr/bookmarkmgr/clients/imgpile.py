from http import HTTPStatus
from typing import (
    cast,
    Literal,
    NewType,
    NotRequired,
    TYPE_CHECKING,
    TypedDict,
)

from aiohttp import FormData

from bookmarkmgr.aiohttp import RateLimitedRetryClientSession
from bookmarkmgr.asyncio import RateLimiter
from bookmarkmgr.clients import ClientSessionContextManagerMixin
from bookmarkmgr.types import Failure, Result, Success

if TYPE_CHECKING:
    from io import IOBase

ApiKey = NewType("ApiKey", str)

ErrorCode = Literal["unauthorized"]


class _Error(TypedDict):
    code: ErrorCode
    message: str


class ErrorErrorResponse(TypedDict):
    error: _Error


class MessageErrorResponse(TypedDict):
    errors: NotRequired[dict[str, list[str]]]
    message: str


type ErrorResponse = ErrorErrorResponse | MessageErrorResponse


class _Urls(TypedDict):
    original: str


class _Data(TypedDict):
    urls: _Urls


class UploadSuccessfulResponse(TypedDict):
    data: _Data


type UploadResult = Result[UploadSuccessfulResponse, ErrorResponse]


class Client(
    ClientSessionContextManagerMixin[RateLimitedRetryClientSession],
):
    def __init__(self, api_key: ApiKey) -> None:
        self._session = RateLimitedRetryClientSession(
            headers={
                "Authorization": f"Bearer {api_key}",
            },
            rate_limiter=RateLimiter(
                limit=120,
                period=60,
            ),
        )

    async def upload(self, file_contents: IOBase) -> UploadResult:
        form_data = FormData(default_to_multipart=True)
        form_data.add_field("file", file_contents)

        async with await self._session.post(
            "https://imgpile.com/uploads",
            data=form_data,
            raise_for_status=False,
        ) as response:
            response_json = await response.json()

        if response.status == HTTPStatus.CREATED:
            return Success(cast("UploadSuccessfulResponse", response_json))

        return Failure(cast("ErrorResponse", response_json))
