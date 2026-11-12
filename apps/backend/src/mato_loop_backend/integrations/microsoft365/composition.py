from __future__ import annotations

import base64
import binascii
import hashlib
import html
import json
import re
from io import BytesIO

from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError

MAX_SIGNATURE_IMAGE_BYTES = 200 * 1024
MAX_SIGNATURE_BYTES = 400 * 1024
MAX_OUTGOING_BYTES = 150 * 1024 * 1024
SIMPLE_ATTACHMENT_BYTES = 3 * 1024 * 1024
MAX_JSON_BYTES = 4_000_000
_TAGS = {
    "a", "b", "br", "div", "em", "font", "i", "img", "li", "ol", "p", "s", "span",
    "strike", "strong", "u", "ul", "table", "thead", "tbody", "tfoot", "tr", "td", "th",
}
_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "width", "height"},
    "font": {"face", "color", "size"},
    "table": {"width", "height", "border", "cellpadding", "cellspacing", "align", "bgcolor", "role"},
    "td": {"width", "height", "align", "valign", "colspan", "rowspan", "bgcolor"},
    "th": {"width", "height", "align", "valign", "colspan", "rowspan", "bgcolor"},
    "tr": {"align", "valign", "bgcolor"},
}
_STYLES = {
    "color", "background-color", "font-family", "font-size", "font-weight", "font-style",
    "line-height", "text-align", "text-decoration", "vertical-align", "white-space",
    "width", "height", "min-width", "max-width", "border-collapse", "border-spacing",
    "border-color", "border-style", "border-width",
    *(f"{name}{side}" for name in ("margin", "padding", "border") for side in ("", "-top", "-right", "-bottom", "-left")),
}
_IMAGE_FORMATS = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}


def _style(value: str) -> str:
    if len(value) > 2000:
        raise ValueError("Signature styles exceed the limit")
    declarations = []
    for declaration in value.split(";"):
        if not declaration.strip():
            continue
        name, separator, content = declaration.partition(":")
        name, content = name.strip().lower(), content.strip()
        plain = re.sub(r"\b(?:rgba?|hsla?)\([0-9.,%+\s-]+\)", "0", content, flags=re.I)
        if not separator or name not in _STYLES or not re.fullmatch(r"""[a-z0-9#.,%'"\s-]+""", plain, re.I):
            raise ValueError("Unsupported signature style")
        declarations.append(f"{name}:{content}")
    return ";".join(declarations)


def _valid_attribute(tag: str, name: str, value: str) -> bool:
    if name in {"width", "height"}:
        return (
            bool(re.fullmatch(r"[0-9]+", value)) and 1 <= int(value) <= 2048
            or tag != "img" and bool(re.fullmatch(r"[0-9]+%", value)) and 1 <= int(value[:-1]) <= 100
        )
    if name in {"border", "cellpadding", "cellspacing", "colspan", "rowspan"}:
        return bool(re.fullmatch(r"[0-9]{1,2}", value))
    if name == "align":
        return value.lower() in {"left", "center", "right", "justify"}
    if name == "valign":
        return value.lower() in {"top", "middle", "bottom", "baseline"}
    if name == "role":
        return value == "presentation"
    if name == "face":
        return len(value) <= 200 and bool(re.fullmatch(r"[a-z0-9 ,'-]+", value, re.I))
    if name == "size":
        return bool(re.fullmatch(r"[+-]?[1-7]", value))
    if name in {"color", "bgcolor"}:
        return bool(re.fullmatch(r"#[a-f0-9]{3,8}|[a-z]+", value, re.I))
    return True


def _image(source: str) -> tuple[str, bytes]:
    try:
        header, encoded = source.split(",", 1)
        content_type = header.removeprefix("data:").removesuffix(";base64")
        if header != f"data:{content_type};base64" or content_type not in _IMAGE_FORMATS:
            raise ValueError("Unsupported signature image")
        data = base64.b64decode(re.sub(r"[\t\n\f\r ]", "", encoded), validate=True)
        if not data or len(data) > MAX_SIGNATURE_IMAGE_BYTES:
            raise ValueError("Signature image exceeds 200 KiB")
        with Image.open(BytesIO(data)) as image:
            if image.format != _IMAGE_FORMATS[content_type] or max(image.size) > 2048:
                raise ValueError("Unsupported signature image")
            image.verify()
        return content_type, data
    except (binascii.Error, UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid signature image") from exc


def validate_signature(value: str) -> str:
    soup = BeautifulSoup(value, "html.parser")
    total, count = 0, 0
    for tag in soup.find_all(True):
        allowed = _ATTRIBUTES.get(tag.name, set()) | {"style"}
        if tag.name not in _TAGS or set(tag.attrs) - allowed:
            raise ValueError("Unsupported signature markup")
        for name, value in tag.attrs.items():
            if not _valid_attribute(tag.name, name, str(value)):
                raise ValueError("Unsupported signature attribute")
        if "style" in tag.attrs:
            tag["style"] = _style(str(tag["style"]))
        if tag.name == "a" and "href" in tag.attrs and not str(tag["href"]).lower().startswith(("https://", "http://", "mailto:")):
            raise ValueError("Unsupported signature link")
        if tag.name == "img":
            content_type, data = _image(str(tag.get("src", "")))
            tag["src"] = f"data:{content_type};base64," + base64.b64encode(data).decode("ascii")
            total += len(data)
            count += 1
    if total > MAX_SIGNATURE_BYTES or count > 8:
        raise ValueError("Signature images exceed the limit")
    rendered = str(soup)
    if len(rendered) > 750_000:
        raise ValueError("Signature exceeds the markup limit")
    return rendered


def signature_content(value: str) -> tuple[str, list[dict]]:
    soup = BeautifulSoup(value, "html.parser")
    images = {}
    for tag in soup.find_all("img"):
        content_type, data = _image(str(tag["src"]))
        content_id = hashlib.sha256(data).hexdigest() + "@matoloop-signature"
        tag["src"] = "cid:" + content_id
        images[content_id] = {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": "signature-" + content_id[:12] + "." + content_type.split("/")[1],
            "contentType": content_type,
            "contentBytes": base64.b64encode(data).decode("ascii"),
            "isInline": True,
            "contentId": content_id,
        }
    return str(soup), list(images.values())


def message_payload(
    *, to, cc=(), bcc=(), subject: str, body: str, signature: str = "", attachments=(),
) -> dict:
    signature_html, inline = signature_content(signature)
    if sum(len(item[2]) for item in attachments) + sum(len(base64.b64decode(item["contentBytes"])) for item in inline) > MAX_OUTGOING_BYTES:
        from .client import Microsoft365Error

        raise Microsoft365Error("integration.attachmentTooLarge")
    content = {"contentType": "text", "content": body}
    if signature_html:
        content = {"contentType": "html", "content": "<div>" + html.escape(body).replace("\n", "<br>") + "</div><br>" + signature_html}
    result = {
        "subject": subject,
        "body": content,
        **{
            field: [{"emailAddress": {"address": address}} for address in addresses]
            for field, addresses in (("toRecipients", to), ("ccRecipients", cc), ("bccRecipients", bcc))
            if addresses
        },
    }
    files = [
        {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": name, "contentType": content_type,
            "contentBytes": base64.b64encode(data).decode("ascii"),
        }
        for name, content_type, data in attachments
    ]
    if files or inline:
        result["attachments"] = [*files, *inline]
    if len(json.dumps({key: value for key, value in result.items() if key != "attachments"}, ensure_ascii=False).encode()) > MAX_JSON_BYTES:
        from .client import Microsoft365Error

        raise Microsoft365Error("integration.attachmentTooLarge")
    return result


def reply_body(content: dict[str, str], original: dict[str, str]) -> dict[str, str]:
    if content["contentType"] == original["contentType"] == "text":
        return {"contentType": "text", "content": content["content"] + "\n\n" + original["content"]}

    def markup(body: dict[str, str]) -> str:
        return body["content"] if body["contentType"] == "html" else html.escape(body["content"]).replace("\n", "<br>")

    quoted = BeautifulSoup(markup(original), "html.parser")
    target = quoted.body if quoted.body is not None else quoted
    prefix = BeautifulSoup(markup(content) + "<br><br>", "html.parser")
    for index, node in enumerate(list(prefix.contents)):
        target.insert(index, node)
    return {"contentType": "html", "content": str(quoted)}


def inline_payload(message: dict) -> bool:
    return all(
        len(item["contentBytes"]) < 4 * (SIMPLE_ATTACHMENT_BYTES // 3)
        for item in message.get("attachments", ())
    ) and len(json.dumps({"message": message}, ensure_ascii=False, separators=(",", ":")).encode()) <= MAX_JSON_BYTES
