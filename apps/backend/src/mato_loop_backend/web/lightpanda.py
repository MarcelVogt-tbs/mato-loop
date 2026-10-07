from __future__ import annotations

import asyncio
from dataclasses import replace

from pydantic import BaseModel, ConfigDict, Field
from yarl import URL

from mato_loop_backend.web.fetch import (
    WebFetchError,
    _Response,
    _Target,
    _validate_target,
    canonicalize_url,
)

# ponytail: two renders per backend process; use a worker only if measured load requires it.
_RENDERS = asyncio.Semaphore(2)

# Match fetch._address_is_public (CPython 3.12), including reserved ranges and
# IANA public exceptions. Lightpanda normalizes IPv4-mapped IPv6 before filtering.
# The boundary test detects changes in Python's address classifications.
_PUBLIC_ADDRESS_CIDRS = (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
    "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24",
    "224.0.0.0/3", "-192.0.0.9/32", "-192.0.0.10/32",
    "::/3", "4000::/2", "8000::/1", "2001::/23", "2001:db8::/32",
    "2002::/16", "3fff::/20", "-2001:1::1/128", "-2001:1::2/128",
    "-2001:3::/32", "-2001:4:112::/48", "-2001:20::/28", "-2001:30::/28",
)


class _FetchOutput(BaseModel):
    model_config = ConfigDict(strict=True)

    url: str
    http_status: int = Field(ge=0, le=599)
    content: str = ""
    error: str | None = None


async def render_html(
    target: _Target, *, timeout: float, public_addresses_only: bool,
    max_response_bytes: int, user_agent: str,
) -> tuple[_Target, _Response, float]:
    """Load a page natively; page authorization includes its dependent requests."""
    loop = asyncio.get_running_loop()
    started = loop.time()
    async with asyncio.timeout(max(timeout, 0.001)), _RENDERS:
        milliseconds = str(max(1, int((timeout - (loop.time() - started)) * 1000)))
        try:
            process = await asyncio.create_subprocess_exec(
                "/usr/local/bin/lightpanda", "fetch", "--json", "--dump", "html",
                "--dump-max-bytes", str(max_response_bytes + 1),
                "--http-max-response-size", str(max_response_bytes),
                "--user-agent", user_agent, "--load-resources", "stylesheet,worker",
                "--wait-until", "networkidle", "--wait-ms", milliseconds,
                "--terminate-ms", milliseconds,
                "--http-timeout", milliseconds, "--v8-max-heap-mb", "128",
                "--watchdog-ms", "5000",
                *(["--block-private-networks", "--block-cidrs", ",".join(_PUBLIC_ADDRESS_CIDRS)]
                  if public_addresses_only else []),
                str(target.browsing_url or target.url),
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, cwd="/",
                env={"HOME": "/nonexistent", "LIGHTPANDA_DISABLE_TELEMETRY": "true",
                     "LIGHTPANDA_DISABLE_CORE_DUMP": "1"},
            )
        except OSError:
            raise WebFetchError("webfetch.engineUnavailable") from None
        try:
            output = bytearray()
            while chunk := await process.stdout.read(65536):
                # JSON escaping can expand each content byte sixfold; headers are bounded separately.
                if len(output) + len(chunk) > max_response_bytes * 6 + 65536:
                    raise WebFetchError("webfetch.responseTooLarge")
                output.extend(chunk)
            await process.wait()
            try:
                result = _FetchOutput.model_validate_json(output)
            except ValueError:
                raise WebFetchError("webfetch.renderFailed") from None
            if result.error is not None:
                raise WebFetchError("webfetch.renderFailed")
            if result.http_status == 202 or not 200 <= result.http_status < 300:
                raise WebFetchError("webfetch.requestFailed", params={"statusCode": result.http_status})
            if process.returncode != 0:
                raise WebFetchError("webfetch.renderFailed")
            data = result.content.encode()
            if len(data) > max_response_bytes:
                raise WebFetchError("webfetch.responseTooLarge")
            final_target = await _validate_target(result.url, public_addresses_only)
            final_target = replace(final_target, browsing_url=URL(
                canonicalize_url(result.url, preserve_fragment=True), encoded=True,
            ))
            return (final_target, _Response(result.http_status, None, "text/html", "utf-8", None, data),
                    max(0, timeout - (loop.time() - started)))
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()
