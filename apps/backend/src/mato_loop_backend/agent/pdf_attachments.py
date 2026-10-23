"""Prepare bounded upload previews using the existing run-local image path."""

from __future__ import annotations

import asyncio

from mato_loop_backend.media.attachments import AttachmentError, AttachmentService
from mato_loop_backend.media.pdf import PdfVisionHandler, initial_pdf_pages, render_pdf_page
from mato_loop_backend.media.processing import AttachmentProcessingError
from mato_loop_backend.providers.base import (
    MAX_PROVIDER_IMAGE_BYTES,
    MAX_PROVIDER_IMAGES,
    ProviderError,
    ProviderImageInput,
)
from mato_loop_backend.settings.models import TrustLevel
from mato_loop_backend.web.fetch import WebFetchError


async def prepare_pdf_attachments(
    records, service: AttachmentService, *, images: dict, cache: dict,
    supports_images: bool, trust: TrustLevel, handler: PdfVisionHandler | None,
) -> dict[str, dict]:
    latest = next((record for record in reversed(records) if record.role == "user"), None)
    attachments = [item for item in latest.attachments if item.mime_type == "application/pdf"
                   and item.processing_status == "ready" and not (item.metadata_json or {}).get("protectedInput")] if latest else []
    for key in list(cache):
        if latest is None or key[0] != latest.id:
            del cache[key]
    # Reinsert current previews from verified sources before budgeting against tool images.
    for key in list(images):
        if key[2].startswith("pdf-upload:"):
            del images[key]
    live_keys = set()
    notes = {}
    remaining_count, remaining_bytes = MAX_PROVIDER_IMAGES, MAX_PROVIDER_IMAGE_BYTES
    for item in attachments:
        key = (latest.id, item.id, item.sha256, supports_images, handler is not None)
        try:
            # Recheck current-only access before reusing an upload preview.
            data = await asyncio.to_thread(service.read_source, item, latest.session_id, current_only=True)
        except AttachmentError:
            continue
        live_keys.add(key)
        if key not in cache:
            rendered, descriptions = [], {}
            try:
                if supports_images or handler is not None:
                    for number in initial_pdf_pages((item.metadata_json or {}).get("pageCount", 1))[:remaining_count]:
                        image = await asyncio.to_thread(render_pdf_page, data, number)
                        if len(image) > remaining_bytes:
                            break
                        rendered.append((number, image))
                        remaining_bytes -= len(image)
                    if not supports_images and rendered:
                        descriptions = await handler(rendered, trust == TrustLevel.PRIVATE, focus=latest.content)
                cache[key] = (rendered if supports_images else [], descriptions)
            except (AttachmentProcessingError, WebFetchError, ProviderError):
                cache[key] = ([], {"status": "visual_analysis_failed"})
        rendered, descriptions = cache[key]
        remaining_count -= len(rendered) if supports_images else len(descriptions)
        if supports_images:
            remaining_bytes = MAX_PROVIDER_IMAGE_BYTES - sum(len(image) for value in cache.values() for _, image in value[0])
        notes[str(item.id)] = {
            "path": item.file_path, "pageCount": (item.metadata_json or {}).get("pageCount"),
            "previewPages": [number for number, _ in rendered] or [number for number in descriptions if type(number) is int],
            "observations": descriptions,
            "note": "Only supplied images or visual observations provide visual coverage. Use read with pdf_pages for other pages.",
        }
        for number, data in rendered:
            image_key = (latest.id, 0, f"pdf-upload:{item.id}", number)
            while images and (len(images) >= MAX_PROVIDER_IMAGES or sum(len(image.data) for image in images.values()) + len(data) > MAX_PROVIDER_IMAGE_BYTES):
                images.pop(next(iter(images)))
            images[image_key] = ProviderImageInput(
                0, data, trust.value, filename=f"{item.filename}, PDF page {number}",
            )
    for key in list(cache):
        if key not in live_keys:
            del cache[key]
    return notes
