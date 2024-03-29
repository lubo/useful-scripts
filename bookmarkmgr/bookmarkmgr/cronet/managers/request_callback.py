from collections.abc import Callable
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

        manager.result_error = error

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
) -> None:
    manager.response.reason = ffi.string(
        lib.Cronet_UrlResponseInfo_http_status_text_get(
            response_info,
        ),
    ).decode()
    manager.response.status_code = (
        lib.Cronet_UrlResponseInfo_http_status_code_get(response_info)
    )
    if not manager.response.reason:
        with contextlib.suppress(ValueError):
            manager.response.reason = HTTPStatus(
                manager.response.status_code,
            ).phrase

    for index in range(
        lib.Cronet_UrlResponseInfo_all_headers_list_size(response_info),
    ):
        header = lib.Cronet_UrlResponseInfo_all_headers_list_at(
            response_info,
            index,
        )
        manager.response.headers[
            ffi.string(lib.Cronet_HttpHeader_name_get(header)).decode()
        ] = ffi.string(lib.Cronet_HttpHeader_value_get(header)).decode()


@ffi.def_extern()
def _on_request_redirect_received(
    callback: UrlRequestCallback,
    request: UrlRequest,
    response_info: UrlResponseInfo,
    new_location_url: String,
) -> None:
    manager = _get_manager(callback)
    manager.response.redirect_url = ffi.string(new_location_url).decode()

    if not manager.request_parameters.allow_redirects:
        _process_response(manager, response_info)
        lib.Cronet_UrlRequest_Cancel(request)
        return

    if _cancel_request_on_error(
        lib.Cronet_UrlRequest_FollowRedirect(request),
        request,
        manager,
    ):
        return

    manager.response.url = manager.response.redirect_url


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
    manager.response.content += ffi.string(
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
    manager.on_request_finished()


@ffi.def_extern()
def _on_request_failed(
    callback: UrlRequestCallback,
    request: UrlRequest,  # noqa: ARG001
    response_info: UrlResponseInfo,  # noqa: ARG001
    error: Error_,
) -> None:
    manager = _get_manager(callback)
    manager.request_error = RequestError(
        "{}: {} {}".format(
            ffi.string(
                lib.Cronet_Error_message_get(error),
            ).decode(),
            manager.request_parameters.method,
            manager.request_parameters.url,
        ),
        code=lib.Cronet_Error_error_code_get(error),
    )
    manager.on_request_finished()


@ffi.def_extern()
def _on_request_canceled(
    callback: UrlRequestCallback,
    request: UrlRequest,  # noqa: ARG001
    response_info: UrlResponseInfo,  # noqa: ARG001
) -> None:
    manager = _get_manager(callback)
    manager.on_request_finished()


class RequestCallbackManager:
    def __init__(
        self,
        request_parameters: RequestParameters,
        on_request_finished: Callable[[], None],
    ) -> None:
        self._handle = ffi.new_handle(self)
        self._callback: UrlRequestCallback | None = None
        self.on_request_finished = on_request_finished
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

        self.request_error: RequestError | None = None
        self.response = Response(url=self.request_parameters.url)
        self.result_error: Error | None = None

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
