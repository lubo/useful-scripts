from http import HTTPStatus
from typing import cast, NotRequired, TYPE_CHECKING, TypedDict

from aiohttp import FormData

from bookmarkmgr.aiohttp import RateLimitedRetryClientSession
from bookmarkmgr.asyncio import RateLimiter
from bookmarkmgr.clients import ClientSessionContextManagerMixin
from bookmarkmgr.types import Failure, Result, Success

if TYPE_CHECKING:
    from io import IOBase


class ErrorErrorResponse(TypedDict):
    error: str


class MessageErrorResponse(TypedDict):
    errors: NotRequired[dict[str, list[str]]]
    message: str


type ErrorResponse = ErrorErrorResponse | MessageErrorResponse


class MediaUrls(TypedDict):
    original: str


class Media(TypedDict):
    urls: MediaUrls


class MediaCreatedResponse(TypedDict):
    media: Media


type MediaUploadResult = Result[MediaCreatedResponse, ErrorResponse]


class Client(
    ClientSessionContextManagerMixin[RateLimitedRetryClientSession],
):
    def __init__(self, api_key: str) -> None:
        self._session = RateLimitedRetryClientSession(
            headers={
                "Authorization": f"Bearer {api_key}",
            },
            rate_limiter=RateLimiter(
                limit=120,
                period=60,
            ),
        )

    async def media_upload(self, file_contents: IOBase) -> MediaUploadResult:
        form_data = FormData(default_to_multipart=True)
        form_data.add_field("file", file_contents)

        async with await self._session.post(
            "https://cdn.imgpile.com/api/v1/media",
            data=form_data,
            raise_for_status=False,
        ) as response:
            response_json = await response.json()  # type: ignore[misc]

        if response.status == HTTPStatus.CREATED:
            return Success(cast("MediaCreatedResponse", response_json))

        return Failure(cast("ErrorResponse", response_json))
