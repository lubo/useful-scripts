import shutil
from typing import Self, TYPE_CHECKING

from .errors import NotContextManagerError

if TYPE_CHECKING:
    from playwright.async_api import (
        Browser,
        Playwright,
    )


BROWSER_EXECUTABLE = "brave"


class BrowserManager:
    def __init__(self, playwright: Playwright) -> None:
        self._browser: Browser | None = None
        self._playwright = playwright

    async def __aenter__(self) -> Self:
        if self._browser is not None:
            return self

        if not (executable_path := shutil.which(BROWSER_EXECUTABLE)):
            message = f"Executable not found: {BROWSER_EXECUTABLE}"
            raise RuntimeError(message)

        self._browser = await self._playwright.chromium.launch(
            executable_path=executable_path,
            # Browser closing is handled by this context manager. Without this,
            # the browser would be closed prematurely, leading to error:
            #
            #   Connection closed while reading from the driver
            handle_sigint=False,
            handle_sigterm=False,
            handle_sighup=False,
            headless=True,
            chromium_sandbox=True,  # The default is insecure.
        )

        return self

    async def __aexit__(self, *_: object) -> None:
        if self._browser is not None:
            await self._browser.close()

    def get_browser(self) -> Browser:
        if self._browser is None:
            raise NotContextManagerError

        return self._browser
