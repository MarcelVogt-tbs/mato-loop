from __future__ import annotations

import base64
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, TypeAdapter, field_validator, model_validator

from .protocol import UUID_PATTERN, StrictModel

Id = Annotated[str, Field(pattern=UUID_PATTERN)]
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_CHUNK_BYTES = 65_536
MAX_CLIPBOARD_BYTES = 16 * 1024
BrowserErrorCode = Literal[
    "browser.unavailable",
    "browser.notReady",
    "browser.busy",
    "browser.notConnected",
    "browser.bindingChanged",
    "browser.setupFailed",
    "browser.dependenciesMissing",
    "browser.unsupported",
    "browser.sandboxUnavailable",
    "browser.actionFailed",
    "browser.invalidTarget",
    "browser.fileTooLarge",
    "browser.profileOwnerMismatch",
    "browser.profileInUse",
    "browser.outcomeUnknown",
]


def decode_chunk(value: str) -> bytes:
    data = base64.b64decode(value, validate=True)
    if len(data) > MAX_CHUNK_BYTES:
        raise ValueError("Chunk exceeds 65536 bytes")
    return data


class BrowserStatusArguments(StrictModel):
    pass


class BrowserOwnerArguments(StrictModel):
    owner_id: Id = Field(alias="ownerId")


class BrowserIdArguments(StrictModel):
    browser_id: Id = Field(alias="browserId")


class BrowserOpenArguments(BrowserOwnerArguments, BrowserIdArguments):
    allow_network: bool = Field(default=True, alias="allowNetwork")


class BrowserViewArguments(BrowserIdArguments):
    relay_id: Id = Field(alias="relayId")
    relay_token: str = Field(alias="relayToken", min_length=16, max_length=8192)


class BrowserControlArguments(BrowserIdArguments):
    manual: bool


class BrowserResizeResult(StrictModel):
    width: int = Field(ge=320, le=3840)
    height: int = Field(ge=240, le=2160)


class BrowserResizeArguments(BrowserIdArguments, BrowserResizeResult):
    pass


class BrowserClipboardResult(StrictModel):
    text: str = Field(max_length=MAX_CLIPBOARD_BYTES)

    @field_validator("text")
    @classmethod
    def bounded_utf8(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_CLIPBOARD_BYTES:
            raise ValueError("Clipboard exceeds 16384 UTF-8 bytes")
        return value


class BrowserClipboardArguments(BrowserIdArguments):
    action: Literal["read", "write", "copy"]
    text: str | None = Field(default=None, max_length=MAX_CLIPBOARD_BYTES)

    @model_validator(mode="after")
    def valid_text(self):
        if self.action == "write":
            if self.text is None:
                raise ValueError("Clipboard write requires text")
            BrowserClipboardResult(text=self.text)
        elif self.text is not None:
            raise ValueError("Clipboard read and copy do not accept text")
        return self


class BrowserSetupIdArguments(StrictModel):
    setup_id: Id = Field(alias="setupId")


class BrowserSetupReadArguments(BrowserSetupIdArguments):
    offset: int = Field(ge=0)
    max_bytes: int = Field(default=MAX_CHUNK_BYTES, alias="maxBytes", ge=1, le=MAX_CHUNK_BYTES)
    wait_seconds: float = Field(default=0, alias="waitSeconds", ge=0, le=5)


class BrowserSetupWriteArguments(BrowserSetupIdArguments):
    data: str = Field(max_length=87_384)

    @field_validator("data")
    @classmethod
    def valid_chunk(cls, value: str) -> str:
        decode_chunk(value)
        return value


class BrowserSetupResizeArguments(BrowserSetupIdArguments):
    rows: int = Field(ge=1, le=1000)
    columns: int = Field(ge=1, le=1000)


class BrowserExecuteArguments(BrowserIdArguments):
    action: Literal[
        "tabs",
        "newtab",
        "closetab",
        "navigate",
        "inspect",
        "screenshot",
        "click",
        "fill",
        "press",
        "scroll",
        "diagnostics",
        "upload",
    ]
    tab_id: Id | None = Field(default=None, alias="tabId")
    url: str | None = Field(default=None, max_length=4096)
    selector: str | None = Field(default=None, max_length=2000)
    text: str | None = Field(default=None, max_length=32_768)
    key: str | None = Field(default=None, max_length=100)
    direction: Literal["up", "down"] | None = None
    download_id: Id | None = Field(default=None, alias="downloadId")
    upload_id: Id | None = Field(default=None, alias="uploadId")
    allow_network: bool = Field(default=True, alias="allowNetwork")
    observe: bool = False

    @field_validator("url")
    @classmethod
    def web_url(cls, value: str | None) -> str | None:
        if value is not None:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or any(ord(c) < 32 for c in value)
            ):
                raise ValueError("Only HTTP(S) navigation is allowed")
        return value

    @model_validator(mode="after")
    def required_fields(self):
        required = {
            "navigate": (self.url,),
            "click": (self.selector,),
            "fill": (self.selector, self.text),
            "press": (self.key,),
            "scroll": (self.direction,),
            "upload": (self.selector, self.upload_id),
        }
        if any(value is None for value in required.get(self.action, ())):
            raise ValueError("Action requires additional fields")
        return self


class BrowserFileArguments(BrowserIdArguments):
    action: Literal["begin", "write", "finish", "read", "delete", "choose"]
    file_id: Id | None = Field(default=None, alias="fileId")
    filename: str | None = Field(default=None, min_length=1, max_length=255)
    size: int | None = Field(default=None, ge=0, le=MAX_FILE_BYTES)
    offset: int = Field(default=0, ge=0, le=MAX_FILE_BYTES)
    data: str = Field(default="", max_length=87_384)
    max_bytes: int = Field(default=MAX_CHUNK_BYTES, alias="maxBytes", ge=1, le=MAX_CHUNK_BYTES)

    @field_validator("data")
    @classmethod
    def valid_chunk(cls, value: str) -> str:
        decode_chunk(value)
        return value

    @field_validator("filename")
    @classmethod
    def plain_filename(cls, value: str | None) -> str | None:
        if value is not None and (
            value in {".", ".."}
            or any(c in value for c in "/\\")
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
        ):
            raise ValueError("Filename must be plain")
        return value

    @model_validator(mode="after")
    def required_fields(self):
        if self.action == "begin":
            if self.filename is None or self.size is None or self.file_id is not None:
                raise ValueError("Begin requires filename and size, not fileId")
        elif self.file_id is None:
            raise ValueError("Action requires fileId")
        return self


class BrowserNavigationArguments(BrowserIdArguments):
    action: Literal[
        "state", "select", "new", "close", "navigate", "back", "forward", "reload", "stop", "chrome"
    ]
    tab_id: Id | None = Field(default=None, alias="tabId")
    url: str | None = Field(default=None, max_length=4096)
    native_chrome: bool | None = Field(default=None, alias="nativeChrome")

    @field_validator("url")
    @classmethod
    def manual_url(cls, value: str | None) -> str | None:
        if value == "about:blank":
            return value
        BrowserExecuteArguments.web_url(value)
        if value is not None:
            parsed = urlsplit(value)
            if "\\" in value or value != value.strip() or parsed.port == 0:
                raise ValueError("Invalid manual navigation URL")
        return value

    @model_validator(mode="after")
    def action_fields(self):
        fields = {
            "state": set(),
            "new": {"url"},
            "chrome": {"native_chrome"},
            "navigate": {"tab_id", "url"},
        }.get(self.action, {"tab_id"})
        supplied = self.model_fields_set - {"browser_id", "action"}
        if supplied - fields or (self.action != "new" and supplied != fields):
            raise ValueError("Invalid navigation action fields")
        if any(getattr(self, field) is None for field in supplied):
            raise ValueError("Navigation fields must not be null")
        return self


class BrowserStatus(StrictModel):
    state: Literal["missing", "installing", "ready", "error", "unsupported"]
    browser_version: str | None = Field(default=None, alias="browserVersion", max_length=100)
    playwright_version: str | None = Field(default=None, alias="playwrightVersion", max_length=100)
    missing: list[str] = Field(default_factory=list, max_length=20)
    setup_id: Id | None = Field(default=None, alias="setupId")
    error_code: BrowserErrorCode | None = Field(default=None, alias="errorCode")


class BrowserSetupReadResult(StrictModel):
    data: str = Field(max_length=87_384)
    offset: int = Field(ge=0)
    running: bool
    exit_code: int | None = Field(alias="exitCode")


class BrowserFileChunkResult(StrictModel):
    file_id: Id = Field(alias="fileId")
    filename: str | None = Field(default=None, max_length=255)
    size: int = Field(ge=0, le=MAX_FILE_BYTES)
    offset: int = Field(ge=0, le=MAX_FILE_BYTES)
    complete: bool
    data: str | None = Field(default=None, max_length=87_384)


class BrowserFileOkResult(StrictModel):
    ok: Literal[True]


BrowserFileResult = BrowserFileChunkResult | BrowserFileOkResult
BROWSER_FILE_RESULT = TypeAdapter(BrowserFileResult)


class BrowserTab(StrictModel):
    id: Id
    url: str = Field(max_length=4096)
    title: str = Field(max_length=500)


class BrowserDownload(StrictModel):
    id: Id
    filename: str = Field(max_length=255)
    size: int = Field(ge=0, le=MAX_FILE_BYTES)


class BrowserNavigationTab(BrowserTab):
    loading: bool


class BrowserNavigationResult(StrictModel):
    tabs: list[BrowserNavigationTab] = Field(max_length=32)
    active_tab_id: Id | None = Field(alias="activeTabId")
    can_go_back: bool = Field(alias="canGoBack")
    can_go_forward: bool = Field(alias="canGoForward")
    loading: bool
    native_chrome: bool = Field(alias="nativeChrome")


class BrowserScreenshot(StrictModel):
    file_id: Id = Field(alias="fileId")
    size: int = Field(ge=0, le=MAX_FILE_BYTES)


class BrowserActionResult(StrictModel):
    tabs: list[BrowserTab] = Field(default_factory=list, max_length=32)
    tab_id: Id | None = Field(default=None, alias="tabId")
    url: str | None = Field(default=None, max_length=4096)
    text: str | None = Field(default=None, max_length=32_768)
    screenshot: BrowserScreenshot | None = None
    downloads: list[BrowserDownload] = Field(default_factory=list, max_length=32)
    data: dict[str, str | bool | int] | None = None


BROWSER_ARGUMENT_MODELS = {
    "integration.browser.status": BrowserStatusArguments,
    "integration.browser.setup": BrowserOwnerArguments,
    "integration.browser.setupread": BrowserSetupReadArguments,
    "integration.browser.setupwrite": BrowserSetupWriteArguments,
    "integration.browser.setupresize": BrowserSetupResizeArguments,
    "integration.browser.open": BrowserOpenArguments,
    "integration.browser.view": BrowserViewArguments,
    "integration.browser.close": BrowserIdArguments,
    "integration.browser.reset": BrowserOwnerArguments,
    "integration.browser.execute": BrowserExecuteArguments,
    "integration.browser.file": BrowserFileArguments,
    "integration.browser.control": BrowserControlArguments,
    "integration.browser.resize": BrowserResizeArguments,
    "integration.browser.clipboard": BrowserClipboardArguments,
    "integration.browser.navigation": BrowserNavigationArguments,
}
