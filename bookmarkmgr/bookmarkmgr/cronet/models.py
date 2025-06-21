from dataclasses import dataclass, field
from email.message import Message
from http import HTTPStatus


@dataclass(slots=True)
class RequestParameters:
    method: str
    url: str
    allow_redirects: bool = True


@dataclass(slots=True)
class Response:
    url: str
    status_code: int
    reason: str
    charset: str = "utf-8"
    content: bytes = b""
    headers: Message = field(default_factory=Message)
    redirect_url: str | None = None

    @property
    def ok(self) -> bool:
        return (
            HTTPStatus.OK.value
            <= self.status_code
            < HTTPStatus.BAD_REQUEST.value
        )

    @property
    def text(self) -> str:
        return self.content.decode(self.charset, "replace")
