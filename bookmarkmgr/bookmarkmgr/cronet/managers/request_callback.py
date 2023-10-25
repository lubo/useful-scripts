from .._cronet import ffi, lib
from ..errors import _raise_for_error_result, Error, RequestError
from ..models import Response


def _cancel_request_on_error(result, request, manager):
    try:
        _raise_for_error_result(result)
    except Error as error:
        lib.Cronet_UrlRequest_Cancel(request)

        manager.result_error = error

        return True

    return False


def _get_manager(callback):
    return ffi.from_handle(
        lib.Cronet_UrlRequestCallback_GetClientContext(callback),
    )


def _process_response(manager, response_info):
    manager.response.reason = ffi.string(
        lib.Cronet_UrlResponseInfo_http_status_text_get(
            response_info,
        ),
    ).decode()
    manager.response.status_code = (
        lib.Cronet_UrlResponseInfo_http_status_code_get(response_info)
    )

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
    callback,
    request,
    response_info,
    new_location_url,
):
    manager = _get_manager(callback)
    manager.response.redirect_url = ffi.string(new_location_url).decode()

    if not manager._request_parameters.allow_redirects:
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
    callback,
    request,
    response_info,
):
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
    callback,
    request,
    response_info,
    buffer,
    bytes_read,
):
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
    callback,
    request,
    response_info,
):
    manager = _get_manager(callback)
    manager._on_request_finished()


@ffi.def_extern()
def _on_request_failed(
    callback,
    request,
    response_info,
    error,
):
    manager = _get_manager(callback)
    manager.request_error = RequestError(
        ffi.string(
            lib.Cronet_Error_message_get(error),
        ).decode(),
        code=lib.Cronet_Error_error_code_get(error),
    )
    manager._on_request_finished()


@ffi.def_extern()
def _on_request_canceled(
    callback,
    request,
    response_info,
):
    manager = _get_manager(callback)
    manager._on_request_finished()


class RequestCallbackManager:
    def __init__(self, request_parameters, on_request_finished):
        self._handle = ffi.new_handle(self)
        self._on_request_finished = on_request_finished
        self._request_parameters = request_parameters
        self.callback = None

    async def __aenter__(self):
        if self.callback is None:
            self.callback = lib.Cronet_UrlRequestCallback_CreateWith(
                lib._on_request_redirect_received,
                lib._on_request_response_started,
                lib._on_request_read_completed,
                lib._on_request_succeeded,
                lib._on_request_failed,
                lib._on_request_canceled,
            )
            lib.Cronet_UrlRequestCallback_SetClientContext(
                self.callback,
                self._handle,
            )

        self.request_error = None
        self.response = Response(url=self._request_parameters.url)
        self.result_error = None

        return self

    async def __aexit__(self, *args, **kwargs):
        lib.Cronet_UrlRequestCallback_Destroy(self.callback)
        self.callback = None
