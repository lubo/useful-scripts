# ruff: noqa: A005

from typing import NewType

Buffer = NewType("Buffer", object)
Engine = NewType("Engine", object)
EngineParams = NewType("EngineParams", object)
Error = NewType("Error", object)
Executor = NewType("Executor", object)
HttpHeader = NewType("HttpHeader", object)
RawData = NewType("RawData", object)
Result = NewType("Result", int)
Runnable = NewType("Runnable", object)
String = NewType("String", object)
UrlRequest = NewType("UrlRequest", object)
UrlRequestCallback = NewType("UrlRequestCallback", object)
UrlResponseInfo = NewType("UrlResponseInfo", object)
UrlRequestParams = NewType("UrlRequestParams", object)
