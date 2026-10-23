"""Bounded PDF page rendering shared by web and managed-file consumers."""

from __future__ import annotations

import subprocess
from collections.abc import Awaitable
from typing import Protocol

from mato_loop_backend.media.processing import AttachmentProcessingError

PDF_RENDER_TIMEOUT_SECONDS = 30
PDF_VISION_BATCH_PAGES = 4


class PdfVisionHandler(Protocol):
    def __call__(
        self, pages: list[tuple[int, bytes]], private_content: bool,
        *, focus: str | None = None,
    ) -> Awaitable[dict[int, str]]: ...


def initial_pdf_pages(page_count: int) -> list[int]:
    return list(range(1, page_count + 1)) if page_count <= PDF_VISION_BATCH_PAGES else [1]


def render_pdf_page(data: bytes, page_number: int) -> bytes:
    if page_number < 1:
        raise AttachmentProcessingError("attachment.invalidDocument")
    try:
        result = subprocess.run(
            [
                "pdftoppm", "-f", str(page_number), "-l", str(page_number),
                "-singlefile", "-scale-to", "1600", "-jpeg", "-jpegopt",
                "quality=85", "-q", "-",
            ],
            input=data, capture_output=True, check=False,
            timeout=PDF_RENDER_TIMEOUT_SECONDS, shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AttachmentProcessingError("attachment.pdfRenderFailed") from exc
    if result.returncode != 0 or not result.stdout.startswith(b"\xff\xd8\xff"):
        raise AttachmentProcessingError("attachment.pdfRenderFailed")
    return result.stdout
