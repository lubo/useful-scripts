from asyncio import Event
import contextlib
from http import HTTPStatus
from typing import Any, cast, Self

from bookmarkmgr.cronet._cronet import ffi, lib
from bookmarkmgr.cronet.errors import (
    _raise_for_error_result,
    Error,
    NotContextManagerError,
    RequestError,
)
from bookmarkmgr.cronet.models import RequestParameters, Response
from bookmarkmgr.cronet.types import (
    Buffer,
    Result,
    String,
    UrlRequest,
    UrlRequestCallback,
    UrlResponseInfo,
)
from bookmarkmgr.cronet.types import (
    Error as Error_,
)


def _cancel_request_on_error(
    result: Result,
    request: UrlRequest,
    manager: "RequestCallbackManager",
) -> bool:
    try:
        _raise_for_error_result(result)
    except Error as error:
        lib.Cronet_UrlRequest_Cancel(request)

        manager._error = error  # noqa: SLF001

        return True

    return False


def _get_manager(callback: UrlRequestCallback) -> "RequestCallbackManager":
    return cast(
        "RequestCallbackManager",
        ffi.from_handle(
            lib.Cronet_UrlRequestCallback_GetClientContext(callback),
        ),
    )


def _process_response(
    manager: "RequestCallbackManager",
    response_info: UrlResponseInfo,
) -> Response:
    previous_response = manager._response  # noqa: SLF001

    url = (
        manager.request_parameters.url
        if previous_response is None or not previous_response.redirect_url
        else previous_response.redirect_url
    )

    status_code = lib.Cronet_UrlResponseInfo_http_status_code_get(
        response_info,
    )

    reason = ffi.string(
        lib.Cronet_UrlResponseInfo_http_status_text_get(
            response_info,
        ),
    ).decode()
    if not reason:
        with contextlib.suppress(ValueError):
            reason = HTTPStatus(status_code).phrase

    response = Response(
        url=url,
        status_code=status_code,
        reason=reason,
    )

    for index in range(
        lib.Cronet_UrlResponseInfo_all_headers_list_size(response_info),
    ):
        header = lib.Cronet_UrlResponseInfo_all_headers_list_at(
            response_info,
            index,
        )
        response.headers[
            ffi.string(lib.Cronet_HttpHeader_name_get(header)).decode()
        ] = ffi.string(lib.Cronet_HttpHeader_value_get(header)).decode()

    manager._response = response  # noqa: SLF001

    return response


@ffi.def_extern()
def _on_request_redirect_received(
    callback: UrlRequestCallback,
    request: UrlRequest,
    response_info: UrlResponseInfo,
    new_location_url: String,
) -> None:
    manager = _get_manager(callback)

    response = _process_response(manager, response_info)
    response.redirect_url = ffi.string(new_location_url).decode()

    if not manager.request_parameters.allow_redirects:
        lib.Cronet_UrlRequest_Cancel(request)
        return

    _cancel_request_on_error(
        lib.Cronet_UrlRequest_FollowRedirect(request),
        request,
        manager,
    )


@ffi.def_extern()
def _on_request_response_started(
    callback: UrlRequestCallback,
    request: UrlRequest,
    response_info: UrlResponseInfo,
) -> None:
    manager = _get_manager(callback)

    _process_response(manager, response_info)

    buffer = lib.Cronet_Buffer_Create()
    lib.Cronet_Buffer_InitWithAlloc(buffer, 32 * 1024)

    _cancel_request_on_error(
        lib.Cronet_UrlRequest_Read(request, buffer),
        request,
        manager,
    )


@ffi.def_extern()
def _on_request_read_completed(
    callback: UrlRequestCallback,
    request: UrlRequest,
    response_info: UrlResponseInfo,  # noqa: ARG001
    buffer: Buffer,
    bytes_read: int,
) -> None:
    manager = _get_manager(callback)

    response = cast("Response", manager._response)  # noqa: SLF001
    response.content += ffi.string(
        ffi.cast("char*", lib.Cronet_Buffer_GetData(buffer)),
        bytes_read,
    )

    _cancel_request_on_error(
        lib.Cronet_UrlRequest_Read(request, buffer),
        request,
        manager,
    )


@ffi.def_extern()
def _on_request_succeeded(
    callback: UrlRequestCallback,
    request: UrlRequest,  # noqa: ARG001
    response_info: UrlResponseInfo,  # noqa: ARG001
) -> None:
    manager = _get_manager(callback)
    manager._is_done.set()  # noqa: SLF001


@ffi.def_extern()
def _on_request_failed(
    callback: UrlRequestCallback,
    request: UrlRequest,  # noqa: ARG001
    response_info: UrlResponseInfo,  # noqa: ARG001
    error: Error_,
) -> None:
    manager = _get_manager(callback)
    manager._error = RequestError(  # noqa: SLF001
        "{}: {} {}".format(
            ffi.string(
                lib.Cronet_Error_message_get(error),
            ).decode(),
            manager.request_parameters.method,
            manager.request_parameters.url,
        ),
        code=lib.Cronet_Error_error_code_get(error),
    )
    manager._is_done.set()  # noqa: SLF001


@ffi.def_extern()
def _on_request_canceled(
    callback: UrlRequestCallback,
    request: UrlRequest,  # noqa: ARG001
    response_info: UrlResponseInfo,  # noqa: ARG001
) -> None:
    manager = _get_manager(callback)
    manager._is_done.set()  # noqa: SLF001


class RequestCallbackManager:
    def __init__(
        self,
        request_parameters: RequestParameters,
    ) -> None:
        self._handle = ffi.new_handle(self)
        self._callback: UrlRequestCallback | None = None
        self._is_done = Event()
        self.request_parameters = request_parameters

    async def __aenter__(self) -> Self:
        if self._callback is None:
            self._callback = lib.Cronet_UrlRequestCallback_CreateWith(
                lib._on_request_redirect_received,  # noqa: SLF001
                lib._on_request_response_started,  # noqa: SLF001
                lib._on_request_read_completed,  # noqa: SLF001
                lib._on_request_succeeded,  # noqa: SLF001
                lib._on_request_failed,  # noqa: SLF001
                lib._on_request_canceled,  # noqa: SLF001
            )
            lib.Cronet_UrlRequestCallback_SetClientContext(
                self._callback,
                self._handle,
            )

        self._is_done.clear()

        self._error: Exception | None = None
        self._response: Response | None = None

        return self

    async def __aexit__(
        self,
        *args: Any,  # noqa: PYI036
        **kwargs: Any,
    ) -> None:
        if self._callback is None:
            return

        lib.Cronet_UrlRequestCallback_Destroy(self._callback)
        self._callback = None

    @property
    def callback(self) -> UrlRequestCallback:
        if self._callback is None:
            raise NotContextManagerError

        return self._callback

    async def response(self) -> Response:
        if not self._is_done.is_set():
            await self._is_done.wait()

        if self._error is not None:
            raise self._error

        if self._response is None:
            message = "Response is unavailable, request may not have finished"
            raise Error(message)

        return self._response
