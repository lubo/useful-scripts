from dataclasses import dataclass, field
from email.message import Message
from http import HTTPStatus
import json
from typing import Any
from urllib.request import Request


@dataclass(slots=True)
class ResponseStatus:
    status_code: int

    @property
    def ok(self) -> bool:
        return (
            HTTPStatus.OK.value
            <= self.status_code
            < HTTPStatus.BAD_REQUEST.value
        )


class RequestParameters(Request):
    @property
    def url(self) -> str:
        return self.full_url


@dataclass(slots=True)
class Response(ResponseStatus):
    url: str
    reason: str
    charset: str = "utf-8"
    content: bytes = b""
    headers: Message = field(default_factory=Message)
    redirect_url: str | None = None

    def info(self) -> Message:
        return self.headers

    def json(self) -> Any:
        return json.loads(self.text)

    @property
    def text(self) -> str:
        return self.content.decode(self.charset, "replace")
