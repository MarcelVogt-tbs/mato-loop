from __future__ import annotations

import asyncio
import codecs
import html
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.message import Message
from pathlib import PurePosixPath
from urllib.parse import unquote

import aiohttp
from aiohttp.abc import AbstractResolver
from bs4 import BeautifulSoup
from bs4.element import Tag
from markdownify import markdownify
from yarl import URL

MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_IMAGE_TYPES = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_TEXT_TYPES = {"application/xhtml+xml", "text/html", "text/markdown", "text/plain"}
_METADATA_ADDRESSES = {
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("169.254.170.2"),
    ipaddress.ip_address("fd00:ec2::254"),
}
_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fec0::/10"),
)
_DROP_HTML_TAGS = {
    "base",
    "canvas",
    "embed",
    "iframe",
    "link",
    "math",
    "meta",
    "noscript",
    "object",
    "script",
    "style",
    "svg",
    "template",
}
_UNWRAP_HTML_TAGS = {"button", "fieldset", "form", "input", "option", "select", "textarea"}
_URL_ATTRIBUTES = {"action", "formaction", "href", "poster", "src", "xlink:href"}
_UNSAFE_URL_SCHEME = re.compile(r"^[\x00-\x20]*(?:data|javascript|vbscript):", re.IGNORECASE)
_CHARSET_PATTERN = re.compile(br"charset\s*=\s*['\"]?\s*([a-zA-Z0-9._:+-]+)", re.IGNORECASE)


class WebFetchError(RuntimeError):
    def __init__(self, code: str, *, params: dict[str, object] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


@dataclass(frozen=True, slots=True)
class WebFetchResult:
    final_url: str
    content_type: str
    content: str | bytes
    is_image: bool
    status_code: int
    output_format: str
    title: str | None = None
    image_mime_type: str | None = None
    image_filename: str | None = None


@dataclass(frozen=True, slots=True)
class _Target:
    url: URL
    host: str
    port: int
    addresses: tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]


@dataclass(frozen=True, slots=True)
class _Response:
    status: int
    location: str | None
    content_type: str
    charset: str | None
    content_disposition: str | None
    body: bytes


class _PinnedResolver(AbstractResolver):
    def __init__(
        self,
        host: str,
        addresses: tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...],
    ) -> None:
        self._host = host
        self._addresses = addresses

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_UNSPEC
    ) -> list[dict[str, object]]:
        if host.rstrip(".").lower() != self._host:
            raise OSError("resolver host mismatch")
        return [
            {
                "hostname": host,
                "host": str(address),
                "port": port,
                "family": socket.AF_INET6 if address.version == 6 else socket.AF_INET,
                "proto": socket.IPPROTO_TCP,
                "flags": 0,
            }
            for address in self._addresses
            if family in {socket.AF_UNSPEC, socket.AF_INET6 if address.version == 6 else socket.AF_INET}
        ]

    async def close(self) -> None:
        return None


def _error(code: str, **params: object) -> WebFetchError:
    return WebFetchError(f"webfetch.{code}", params=params or None)


def _canonical_host(host: str) -> str:
    if not host or any(character.isspace() or ord(character) < 32 for character in host):
        raise _error("invalidUrl")
    if "%" in host:
        raise _error("invalidUrl")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            canonical = host.rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise _error("invalidUrl") from exc
        if not canonical:
            raise _error("invalidUrl") from None
        return canonical
    return str(address.ipv4_mapped or address) if address.version == 6 else str(address)


def _parse_url(value: str, *, origin: bool = False) -> tuple[URL, str, int]:
    if not isinstance(value, str) or not value:
        raise _error("invalidOrigin" if origin else "invalidUrl")
    try:
        url = URL(value)
    except (TypeError, ValueError) as exc:
        raise _error("invalidOrigin" if origin else "invalidUrl") from exc
    if url.scheme.lower() not in {"http", "https"}:
        raise _error("invalidOrigin" if origin else "unsupportedScheme")
    if url.user is not None or url.password is not None:
        raise _error("invalidOrigin" if origin else "userinfoForbidden")
    try:
        host_value = url.host
        port = url.port
    except ValueError as exc:
        raise _error("invalidOrigin" if origin else "invalidUrl") from exc
    if host_value is None or port is None:
        raise _error("invalidOrigin" if origin else "invalidUrl")
    host = _canonical_host(host_value)
    if origin and (url.path not in {"", "/"} or url.query_string or url.fragment):
        raise _error("invalidOrigin")
    try:
        normalized = url.with_scheme(url.scheme.lower()).with_host(host).with_fragment(None)
    except ValueError as exc:
        raise _error("invalidOrigin" if origin else "invalidUrl") from exc
    return normalized, host, port


def canonicalize_url(value: str) -> str:
    url, _, _ = _parse_url(value)
    if url.path == "/":
        url = url.with_path("/", keep_query=True)
    return str(url)


def sanitize_url_for_display(value: str) -> str:
    url, _, _ = _parse_url(canonicalize_url(value))
    if url.query_string:
        url = url.with_query("REDACTED")
    return str(url)


def normalize_source_title(value: object, *, max_length: int = 300) -> str | None:
    if not isinstance(value, str):
        return None
    title = " ".join(value.split())
    if not title:
        return None
    return title[:max_length]


def private_origin_is_allowed(
    value: str, allowed_private_origins: tuple[str, ...] | list[str]
) -> bool:
    url, host, port = _parse_url(canonicalize_url(value))
    return (url.scheme, host, port) in _allowed_origins(allowed_private_origins)


def _allowed_origins(values: tuple[str, ...] | list[str]) -> set[tuple[str, str, int]]:
    if isinstance(values, (str, bytes)):
        raise _error("invalidOrigin")
    origins: set[tuple[str, str, int]] = set()
    try:
        for value in values:
            url, host, port = _parse_url(value, origin=True)
            origins.add((url.scheme, host, port))
    except TypeError as exc:
        raise _error("invalidOrigin") from exc
    return origins


def _normalized_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    address = ipaddress.ip_address(value.split("%", 1)[0])
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _validate_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address, *, private_allowed: bool
) -> None:
    if address in _METADATA_ADDRESSES or address.is_link_local:
        raise _error("addressBlocked")
    if address.is_multicast or address.is_unspecified:
        raise _error("addressBlocked")
    if address.is_loopback:
        if private_allowed:
            return
        raise _error("addressBlocked")
    if any(address in network for network in _PRIVATE_NETWORKS if address.version == network.version):
        if private_allowed:
            return
        raise _error("addressBlocked")
    if address.is_reserved or not address.is_global:
        raise _error("addressBlocked")


async def _resolve_addresses(
    host: str, port: int
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    try:
        literal = _normalized_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return (literal,)
    try:
        records = await asyncio.get_running_loop().getaddrinfo(
            host,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
        addresses = tuple(dict.fromkeys(_normalized_address(record[4][0]) for record in records))
    except (OSError, ValueError) as exc:
        raise _error("dnsFailed") from exc
    if not addresses:
        raise _error("dnsFailed")
    return addresses


async def _validate_target(
    value: str,
    allowed_origins: set[tuple[str, str, int]],
) -> _Target:
    url, host, port = _parse_url(value)
    if url.path == "/":
        url = url.with_path("/", keep_query=True)
    addresses = await _resolve_addresses(host, port)
    private_allowed = (url.scheme, host, port) in allowed_origins
    for address in addresses:
        _validate_address(address, private_allowed=private_allowed)
    return _Target(url=url, host=host, port=port, addresses=addresses)


async def _read_limited(response: aiohttp.ClientResponse) -> bytes:
    if response.content_length is not None and response.content_length > MAX_RESPONSE_BYTES:
        raise _error("responseTooLarge")
    body = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
            raise _error("responseTooLarge")
        body.extend(chunk)
    return bytes(body)


async def _request_once(target: _Target, timeout: float) -> _Response:
    resolver = _PinnedResolver(target.host, target.addresses)
    connector = aiohttp.TCPConnector(
        resolver=resolver,
        use_dns_cache=True,
        ttl_dns_cache=None,
        limit=1,
    )
    headers = {
        "Accept": "text/html, text/markdown, text/plain, image/png, image/jpeg, image/webp, image/gif",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "close",
        "User-Agent": "MatoLoop-WebFetch/1.0",
    }
    try:
        async with aiohttp.ClientSession(
            connector=connector,
            cookie_jar=aiohttp.DummyCookieJar(),
            headers=headers,
            skip_auto_headers={"Cookie"},
            timeout=aiohttp.ClientTimeout(total=timeout),
            trust_env=False,
        ) as session:
            async with session.get(target.url, allow_redirects=False) as response:
                if response.status in _REDIRECT_STATUSES:
                    return _Response(
                        status=response.status,
                        location=response.headers.get("Location"),
                        content_type="",
                        charset=None,
                        content_disposition=None,
                        body=b"",
                    )
                if not 200 <= response.status < 300:
                    return _Response(
                        status=response.status,
                        location=None,
                        content_type="",
                        charset=None,
                        content_disposition=None,
                        body=b"",
                    )
                if (
                    response.content_length is not None
                    and response.content_length > MAX_RESPONSE_BYTES
                ):
                    raise _error("responseTooLarge")
                body = await _read_limited(response)
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
                return _Response(
                    status=response.status,
                    location=None,
                    content_type=content_type.strip().lower(),
                    charset=response.charset,
                    content_disposition=response.headers.get("Content-Disposition"),
                    body=body,
                )
    except WebFetchError:
        raise
    except TimeoutError as exc:
        raise _error("timeout") from exc
    except (aiohttp.ClientError, OSError) as exc:
        raise _error("connectionFailed") from exc


def _decode_text(body: bytes, charset: str | None, *, html_content: bool) -> str:
    encoding = charset
    if not encoding and html_content:
        match = _CHARSET_PATTERN.search(body[:4096])
        encoding = match.group(1).decode("ascii") if match else None
    encoding = encoding or "utf-8"
    try:
        codecs.lookup(encoding)
        return body.decode(encoding, errors="replace")
    except (LookupError, UnicodeError) as exc:
        raise _error("decodeFailed") from exc


def _sanitize_html(value: str) -> BeautifulSoup:
    soup = BeautifulSoup(value, "html.parser")
    for tag in list(soup.find_all(_DROP_HTML_TAGS)):
        tag.decompose()
    for tag in list(soup.find_all(_UNWRAP_HTML_TAGS)):
        tag.unwrap()
    for tag in soup.find_all(True):
        for attribute in list(tag.attrs):
            lowered = attribute.lower()
            if lowered.startswith("on") or lowered in {"srcdoc", "style"}:
                del tag.attrs[attribute]
                continue
            if lowered in _URL_ATTRIBUTES:
                raw_value = tag.attrs[attribute]
                text_value = " ".join(raw_value) if isinstance(raw_value, list) else str(raw_value)
                if _UNSAFE_URL_SCHEME.match(html.unescape(text_value)):
                    del tag.attrs[attribute]
    return soup


def _absolutize_urls(soup: BeautifulSoup, base_url: URL) -> None:
    for tag in soup.find_all(True):
        for attribute in _URL_ATTRIBUTES.intersection(tag.attrs):
            raw_value = tag.attrs[attribute]
            if isinstance(raw_value, list):
                continue
            value = html.unescape(str(raw_value)).strip()
            if not value or value.startswith("#"):
                continue
            try:
                parsed = URL(value)
                if not parsed.scheme:
                    tag.attrs[attribute] = str(base_url.join(parsed))
            except (TypeError, ValueError):
                continue


def _content_root(soup: BeautifulSoup) -> Tag | BeautifulSoup:
    root = (
        soup.find("main")
        or soup.find(attrs={"role": "main"})
        or soup.find("article")
        or soup.body
        or soup
    )
    if root is soup.body or root is soup:
        for tag in list(root.find_all(["nav", "footer"])):
            tag.decompose()
        for tag in list(root.find_all(attrs={"role": "navigation"})):
            tag.decompose()
    return root


def _normalize_lines(value: str) -> str:
    lines = [line.rstrip() for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    result: list[str] = []
    blank = False
    for line in lines:
        if line.strip():
            result.append(line)
            blank = False
        elif result and not blank:
            result.append("")
            blank = True
    return "\n".join(result).strip()


def _convert_text(
    body: bytes,
    content_type: str,
    format: str,
    charset: str | None,
    base_url: URL,
) -> str:
    is_html = content_type in {"application/xhtml+xml", "text/html"}
    value = _decode_text(body, charset, html_content=is_html)
    if is_html:
        soup = _sanitize_html(value)
        _absolutize_urls(soup, base_url)
        if format == "html":
            return str(soup)
        root = _content_root(soup)
        if format == "text":
            plain_text = root.get_text(" ", strip=True)
            return re.sub(r"\s+([,.;:!?])", r"\1", plain_text)
        return _normalize_lines(markdownify(str(root), heading_style="ATX"))
    if format == "html":
        return f"<pre>{html.escape(value)}</pre>"
    if content_type == "text/markdown":
        soup = _sanitize_html(value)
        if format == "text":
            return _normalize_lines(soup.get_text())
        return _normalize_lines(str(soup))
    if format == "markdown":
        return _normalize_lines(html.escape(value, quote=False))
    return _normalize_lines(value)


def _html_title(body: bytes, content_type: str, charset: str | None) -> str | None:
    if content_type not in {"application/xhtml+xml", "text/html"}:
        return None
    value = _decode_text(body, charset, html_content=True)
    soup = BeautifulSoup(value, "html.parser")
    title_tag = soup.head.find("title") if soup.head is not None else None
    if title_tag is None:
        return None
    return normalize_source_title(title_tag.get_text(" ", strip=True))


def _valid_image_signature(content_type: str, body: bytes) -> bool:
    if content_type == "image/png":
        return body.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/jpeg":
        return body.startswith(b"\xff\xd8\xff")
    if content_type == "image/gif":
        return body.startswith((b"GIF87a", b"GIF89a"))
    if content_type == "image/webp":
        return len(body) >= 12 and body.startswith(b"RIFF") and body[8:12] == b"WEBP"
    return False


def _image_filename(url: URL, content_disposition: str | None, content_type: str) -> str:
    filename: str | None = None
    if content_disposition:
        message = Message()
        message["Content-Disposition"] = content_disposition
        filename = message.get_filename()
    if not filename:
        filename = unquote(PurePosixPath(url.path).name)
    filename = re.sub(r"[\x00-\x1f\x7f]", "", (filename or "").replace("\\", "/"))
    filename = PurePosixPath(filename).name.strip(" .")[:240]
    extension = _IMAGE_TYPES[content_type]
    allowed_extensions = {extension, ".jpeg"} if content_type == "image/jpeg" else {extension}
    suffix = PurePosixPath(filename).suffix.lower()
    if not filename:
        filename = f"image{extension}"
    elif suffix not in allowed_extensions:
        filename = f"{PurePosixPath(filename).stem or 'image'}{extension}"
    return filename


def _build_result(target: _Target, response: _Response, format: str) -> WebFetchResult:
    if response.content_type in _IMAGE_TYPES:
        if not _valid_image_signature(response.content_type, response.body):
            raise _error("contentTypeMismatch")
        return WebFetchResult(
            final_url=str(target.url),
            content_type=response.content_type,
            content=response.body,
            is_image=True,
            status_code=response.status,
            output_format="image",
            image_mime_type=response.content_type,
            image_filename=_image_filename(
                target.url, response.content_disposition, response.content_type
            ),
        )
    if response.content_type not in _TEXT_TYPES:
        raise _error("contentTypeUnsupported")
    return WebFetchResult(
        final_url=str(target.url),
        content_type=response.content_type,
        content=_convert_text(
            response.body,
            response.content_type,
            format,
            response.charset,
            target.url,
        ),
        is_image=False,
        status_code=response.status,
        output_format=format,
        title=_html_title(response.body, response.content_type, response.charset),
    )


async def fetch_web(
    url: str,
    format: str = "markdown",
    timeout: float = 30,
    allowed_private_origins: tuple[str, ...] | list[str] = (),
    authorize_url: Callable[[str], Awaitable[None]] | None = None,
) -> WebFetchResult:
    if format not in {"html", "markdown", "text"}:
        raise _error("formatUnsupported")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 120:
        raise _error("timeoutInvalid")
    origins = _allowed_origins(allowed_private_origins)
    loop = asyncio.get_running_loop()
    remaining = float(timeout)
    current_url = url
    try:
        for redirect_count in range(MAX_REDIRECTS + 1):
            current_url = canonicalize_url(current_url)
            if authorize_url is not None:
                await authorize_url(current_url)
            started = loop.time()
            async with asyncio.timeout(max(remaining, 0.001)):
                target = await _validate_target(current_url, origins)
                response = await _request_once(target, remaining)
            remaining -= loop.time() - started
            if response.status in _REDIRECT_STATUSES:
                if not response.location:
                    raise _error("redirectInvalid")
                if redirect_count == MAX_REDIRECTS:
                    raise _error("tooManyRedirects")
                try:
                    current_url = str(target.url.join(URL(response.location)))
                except (TypeError, ValueError) as exc:
                    raise _error("redirectInvalid") from exc
                continue
            if not 200 <= response.status < 300:
                raise _error("requestFailed", statusCode=response.status)
            try:
                return _build_result(target, response, format)
            except WebFetchError as exc:
                if exc.params is None:
                    exc.params = {"statusCode": response.status}
                raise
    except WebFetchError:
        raise
    except TimeoutError as exc:
        raise _error("timeout") from exc
    raise _error("tooManyRedirects")
