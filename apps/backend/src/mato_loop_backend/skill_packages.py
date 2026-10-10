"""Portable Agent Skills packages. SKILL.md is the only content definition."""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import yaml

from mato_loop_backend.files.service import RootFileError, validate_upload_filename

MAX_SKILL_BYTES = 128 * 1024
MAX_PACKAGE_BYTES = 8 * 1024 * 1024
MAX_PACKAGE_FILES = 256
MAX_PACKAGE_DIRECTORIES = MAX_PACKAGE_FILES * 2
MAX_DIRECTORY_DEPTH = 16
MAX_FILE_BYTES = 4 * 1024 * 1024
SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SkillError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def skill_path(path: str) -> str:
    parts = path.split("/")
    if (
        not path or len(path) > 500 or PurePosixPath(path).as_posix() != path
        or any(part in {"", ".", ".."} or part.endswith((".", " ")) for part in parts)
        or any(char in path for char in '\\:<>"|?*')
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
        or any(re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part) for part in parts)
    ):
        raise SkillError("file.invalidPath")
    try:
        for part in parts:
            validate_upload_filename(part)
    except RootFileError as exc:
        raise SkillError(exc.code) from exc
    return path


class _SkillLoader(yaml.SafeLoader):
    pass


def _mapping(loader: _SkillLoader, node: yaml.MappingNode) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise SkillError("skill.draftInvalid")
        result[key] = loader.construct_object(value_node)
    return result


_SkillLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def parse_skill_md(content: bytes | str) -> tuple[dict[str, Any], str]:
    try:
        raw = content.encode("utf-8") if isinstance(content, str) else content
        if len(raw) > MAX_SKILL_BYTES:
            raise SkillError("file.tooLarge")
        text = raw.decode("utf-8-sig")
        match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)(.*)\Z", text, re.DOTALL)
        if match is None:
            raise SkillError("skill.draftInvalid")
        # Aliases are unnecessary for metadata and can create recursive/expanding data.
        if any(isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)) for token in yaml.scan(match[1])):
            raise SkillError("skill.draftInvalid")
        metadata = yaml.load(match[1], Loader=_SkillLoader)
        if not isinstance(metadata, dict):
            raise SkillError("skill.draftInvalid")
        name, description = metadata.get("name"), metadata.get("description")
        if not isinstance(name, str) or len(name) > 64 or not SKILL_NAME.fullmatch(name):
            raise SkillError("skill.nameInvalid")
        try:
            skill_path(name)
        except SkillError as exc:
            raise SkillError("skill.nameInvalid") from exc
        if not isinstance(description, str) or not 1 <= len(description.strip()) <= 1024:
            raise SkillError("skill.draftInvalid")
        for key, limit in (("license", MAX_SKILL_BYTES), ("compatibility", 500), ("allowed-tools", MAX_SKILL_BYTES)):
            if key in metadata and (not isinstance(metadata[key], str) or not 1 <= len(metadata[key]) <= limit):
                raise SkillError("skill.draftInvalid")
        if "metadata" in metadata and not isinstance(metadata["metadata"], dict):
            raise SkillError("skill.draftInvalid")
        # Keep vendor extensions as inert JSON-compatible data, never host configuration.
        json.dumps(metadata, allow_nan=False)
        return metadata, match[2].strip()
    except (UnicodeError, yaml.YAMLError, TypeError, ValueError, RecursionError) as exc:
        if isinstance(exc, SkillError):
            raise
        raise SkillError("skill.draftInvalid") from exc


def skill_md(name: str, description: str, instructions: str, metadata: dict | None = None) -> bytes:
    header = {**(metadata or {}), "name": name, "description": description}
    content = ("---\n" + yaml.safe_dump(header, allow_unicode=True, sort_keys=False).rstrip() + "\n---\n\n" + instructions.strip() + "\n").encode()
    parse_skill_md(content)
    return content


@dataclass(frozen=True)
class SkillFile:
    content: bytes
    executable: bool = False


@dataclass(frozen=True)
class SkillPackage:
    files: dict[str, SkillFile]

    def __post_init__(self) -> None:
        if "SKILL.md" not in self.files or len(self.files) > MAX_PACKAGE_FILES:
            raise SkillError("skill.draftInvalid")
        seen: set[str] = set()
        directories = {PurePosixPath(".")}
        total = 0
        for path, entry in self.files.items():
            skill_path(path)
            directories.update(PurePosixPath(path).parents)
            if path.count("/") > MAX_DIRECTORY_DEPTH or len(directories) > MAX_PACKAGE_DIRECTORIES:
                raise SkillError("file.tooLarge")
            folded = path.casefold()
            if folded in seen or len(entry.content) > MAX_FILE_BYTES:
                raise SkillError("skill.draftInvalid")
            seen.add(folded)
            total += len(entry.content)
        for path in seen:
            if any(parent.as_posix() in seen for parent in PurePosixPath(path).parents if parent.as_posix() != "."):
                raise SkillError("skill.draftInvalid")
        if total > MAX_PACKAGE_BYTES:
            raise SkillError("file.tooLarge")
        parse_skill_md(self.files["SKILL.md"].content)

    @property
    def metadata(self) -> dict[str, Any]:
        return parse_skill_md(self.files["SKILL.md"].content)[0]

    @property
    def name(self) -> str:
        return self.metadata["name"]

    @property
    def revision(self) -> str:
        digest = hashlib.sha256()
        for path, entry in sorted(self.files.items()):
            digest.update(json.dumps([path, entry.executable, len(entry.content)], ensure_ascii=True).encode() + b"\0")
            digest.update(entry.content)
        return digest.hexdigest()

    def archive(self) -> bytes:
        target = io.BytesIO()
        name = self.name
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path, entry in sorted(self.files.items()):
                info = zipfile.ZipInfo(f"{name}/{path}")
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | (0o755 if entry.executable else 0o644)) << 16
                archive.writestr(info, entry.content, compress_type=zipfile.ZIP_DEFLATED)
        return target.getvalue()

    @classmethod
    def import_file(cls, content: bytes, *, archive: bool) -> SkillPackage:
        if len(content) > MAX_PACKAGE_BYTES + 256 * 1024:
            raise SkillError("file.tooLarge")
        if not archive:
            return cls({"SKILL.md": SkillFile(content)})
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as source:
                entries = source.infolist()
                if len(entries) > MAX_PACKAGE_FILES * 2:
                    raise SkillError("file.tooLarge")
                paths = [entry.filename for entry in entries if not entry.is_dir()]
                if "SKILL.md" in paths:
                    prefix = ""
                else:
                    roots = [path[:-8] for path in paths if path.endswith("/SKILL.md") and path.count("/") == 1]
                    if len(roots) != 1:
                        raise SkillError("skill.draftInvalid")
                    prefix = roots[0]
                files = {}
                total = 0
                for entry in entries:
                    skill_path(entry.filename.rstrip("/"))
                    mode = entry.external_attr >> 16
                    kind = stat.S_IFMT(mode)
                    if kind not in {0, stat.S_IFREG, stat.S_IFDIR} or entry.flag_bits & 1:
                        raise SkillError("skill.draftInvalid")
                    if entry.is_dir():
                        continue
                    if not entry.filename.startswith(prefix):
                        raise SkillError("skill.draftInvalid")
                    path = entry.filename[len(prefix):]
                    if path in files or entry.file_size > MAX_FILE_BYTES:
                        raise SkillError("skill.draftInvalid")
                    with source.open(entry) as handle:
                        data = handle.read(min(MAX_FILE_BYTES, MAX_PACKAGE_BYTES - total) + 1)
                    total += len(data)
                    files[path] = SkillFile(data, bool(mode & 0o111))
                    if total > MAX_PACKAGE_BYTES:
                        raise SkillError("file.tooLarge")
                package = cls(files)
                if prefix and prefix != package.name + "/":
                    raise SkillError("skill.nameInvalid")
                return package
        except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, ValueError) as exc:
            if isinstance(exc, SkillError):
                raise
            raise SkillError("skill.draftInvalid") from exc
