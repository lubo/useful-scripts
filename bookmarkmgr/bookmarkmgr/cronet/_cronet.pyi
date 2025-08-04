from collections.abc import Callable
from typing import Any, Literal, NewType

from .types import (
    Buffer,
    Engine,
    EngineParams,
    Error,
    Executor,
    HttpHeader,
    RawData,
    Result,
    Runnable,
    String,
    UrlRequest,
    UrlRequestCallback,
    UrlRequestParams,
    UrlResponseInfo,
)

type _Executor_Execute = Callable[
    [Executor, Runnable],
    None,
]

type _UrlRequestCallback_OnCanceledFunc = Callable[
    [UrlRequestCallback, UrlRequest, UrlResponseInfo],
    None,
]
type _UrlRequestCallback_OnFailedFunc = Callable[
    [UrlRequestCallback, UrlRequest, UrlResponseInfo, Error],
    None,
]
type _UrlRequestCallback_OnReadCompletedFunc = Callable[
    [UrlRequestCallback, UrlRequest, UrlResponseInfo, Buffer, int],
    None,
]
type _UrlRequestCallback_OnRedirectReceivedFunc = Callable[
    [UrlRequestCallback, UrlRequest, UrlResponseInfo, String],
    None,
]
type _UrlRequestCallback_OnResponseStartedFunc = Callable[
    [UrlRequestCallback, UrlRequest, UrlResponseInfo],
    None,
]
type _UrlRequestCallback_OnSucceededFunc = Callable[
    [UrlRequestCallback, UrlRequest, UrlResponseInfo],
    None,
]

_Handle = NewType("_Handle", object)

# ruff: noqa: N802

class _FFI:
    def cast(self, c_type: Literal["char*"], value: Any) -> String: ...
    def def_extern(self) -> Callable[..., None]: ...
    def from_handle(self, handle: _Handle) -> Any: ...
    def new_handle(self, obj: Any) -> _Handle: ...
    def string(self, cdata: String, maxlen: int = ...) -> bytes: ...

class _Lib:
    _executor_execute: _Executor_Execute

    _on_request_canceled: _UrlRequestCallback_OnCanceledFunc
    _on_request_failed: _UrlRequestCallback_OnFailedFunc
    _on_request_read_completed: _UrlRequestCallback_OnReadCompletedFunc
    _on_request_redirect_received: _UrlRequestCallback_OnRedirectReceivedFunc
    _on_request_response_started: _UrlRequestCallback_OnResponseStartedFunc
    _on_request_succeeded: _UrlRequestCallback_OnSucceededFunc

    Cronet_RESULT_SUCCESS: Result

    def Cronet_Buffer_Create(self) -> Buffer: ...
    def Cronet_Buffer_GetData(self, buffer: Buffer) -> RawData: ...
    def Cronet_Buffer_InitWithAlloc(
        self,
        buffer: Buffer,
        size: int,
    ) -> None: ...
    def Cronet_Engine_Create(self) -> Engine: ...
    def Cronet_Engine_Destroy(self, engine: Engine) -> None: ...
    def Cronet_Engine_Shutdown(self, engine: Engine) -> Result: ...
    def Cronet_Engine_StartWithParams(
        self,
        engine: Engine,
        params: EngineParams,
    ) -> Result: ...
    def Cronet_EngineParams_Create(self) -> EngineParams: ...
    def Cronet_EngineParams_Destroy(self, params: EngineParams) -> None: ...
    def Cronet_EngineParams_enable_brotli_set(
        self,
        params: EngineParams,
        enable: bool,
    ) -> None: ...
    def Cronet_EngineParams_enable_http2_set(
        self,
        params: EngineParams,
        enable: bool,
    ) -> None: ...
    def Cronet_EngineParams_enable_quic_set(
        self,
        params: EngineParams,
        enable: bool,
    ) -> None: ...
    def Cronet_Error_error_code_get(self, error: Error) -> int: ...
    def Cronet_Error_message_get(self, error: Error) -> String: ...
    def Cronet_Executor_CreateWith(
        self,
        func: _Executor_Execute,
    ) -> Executor: ...
    def Cronet_Executor_Destroy(self, executor: Executor) -> None: ...
    def Cronet_Executor_GetClientContext(
        self,
        executor: Executor,
    ) -> _Handle: ...
    def Cronet_Executor_SetClientContext(
        self,
        executor: Executor,
        context: _Handle,
    ) -> None: ...
    def Cronet_HttpHeader_Create(self) -> HttpHeader: ...
    def Cronet_HttpHeader_Destroy(self, request: HttpHeader) -> None: ...
    def Cronet_HttpHeader_name_get(self, header: HttpHeader) -> String: ...
    def Cronet_HttpHeader_name_set(
        self,
        header: HttpHeader,
        name: bytes,
    ) -> None: ...
    def Cronet_HttpHeader_value_get(self, header: HttpHeader) -> String: ...
    def Cronet_HttpHeader_value_set(
        self,
        header: HttpHeader,
        value: bytes,
    ) -> None: ...
    def Cronet_Runnable_Destroy(self, runnable: Runnable) -> None: ...
    def Cronet_Runnable_Run(self, runnable: Runnable) -> None: ...
    def Cronet_UrlRequest_Cancel(self, request: UrlRequest) -> None: ...
    def Cronet_UrlRequest_Create(self) -> UrlRequest: ...
    def Cronet_UrlRequest_Destroy(self, request: UrlRequest) -> None: ...
    def Cronet_UrlRequest_FollowRedirect(
        self,
        request: UrlRequest,
    ) -> Result: ...
    def Cronet_UrlRequest_InitWithParams(
        self,
        request: UrlRequest,
        engine: Engine,
        url: bytes,
        params: UrlRequestParams,
        callback: UrlRequestCallback,
        executor: Executor,
    ) -> Result: ...
    def Cronet_UrlRequest_Read(
        self,
        request: UrlRequest,
        buffer: Buffer,
    ) -> Result: ...
    def Cronet_UrlRequest_Start(self, request: UrlRequest) -> Result: ...
    def Cronet_UrlRequestCallback_CreateWith(
        self,
        on_redirect_received: _UrlRequestCallback_OnRedirectReceivedFunc,
        on_response_started: _UrlRequestCallback_OnResponseStartedFunc,
        on_read_completed: _UrlRequestCallback_OnReadCompletedFunc,
        on_succeeded: _UrlRequestCallback_OnSucceededFunc,
        on_failed: _UrlRequestCallback_OnFailedFunc,
        on_canceled: _UrlRequestCallback_OnCanceledFunc,
    ) -> UrlRequestCallback: ...
    def Cronet_UrlRequestCallback_Destroy(
        self,
        callback: UrlRequestCallback,
    ) -> None: ...
    def Cronet_UrlRequestCallback_GetClientContext(
        self,
        callback: UrlRequestCallback,
    ) -> _Handle: ...
    def Cronet_UrlRequestCallback_SetClientContext(
        self,
        callback: UrlRequestCallback,
        context: _Handle,
    ) -> None: ...
    def Cronet_UrlRequestParams_Create(self) -> UrlRequestParams: ...
    def Cronet_UrlRequestParams_Destroy(
        self,
        params: UrlRequestParams,
    ) -> None: ...
    def Cronet_UrlRequestParams_http_method_set(
        self,
        params: UrlRequestParams,
        method: bytes,
    ) -> None: ...
    def Cronet_UrlRequestParams_request_headers_add(
        self,
        params: UrlRequestParams,
        header: HttpHeader,
    ) -> None: ...
    def Cronet_UrlResponseInfo_all_headers_list_at(
        self,
        response_info: UrlResponseInfo,
        index: int,
    ) -> HttpHeader: ...
    def Cronet_UrlResponseInfo_all_headers_list_size(
        self,
        response_info: UrlResponseInfo,
    ) -> int: ...
    def Cronet_UrlResponseInfo_http_status_code_get(
        self,
        response_info: UrlResponseInfo,
    ) -> int: ...
    def Cronet_UrlResponseInfo_http_status_text_get(
        self,
        response_info: UrlResponseInfo,
    ) -> String: ...

ffi = _FFI()
lib = _Lib()
