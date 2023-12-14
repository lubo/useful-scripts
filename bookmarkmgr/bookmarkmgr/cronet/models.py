from dataclasses import dataclass, field
from email.message import Message
from http import HTTPStatus


@dataclass
class RequestParameters:
    method: str
    url: str
    allow_redirects: bool = True


@dataclass
class Response:
    url: str
    charset = "utf-8"
    content = b""
    headers: Message = field(default_factory=Message)
    reason = ""
    redirect_url = ""
    status_code = None

    @property
    def ok(self):
        return (
            HTTPStatus.OK.value
            <= self.status_code
            < HTTPStatus.BAD_REQUEST.value
        )

    @property
    def text(self):
        return self.content.decode(self.charset, "replace")
