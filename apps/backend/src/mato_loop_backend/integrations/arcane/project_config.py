from __future__ import annotations

import hashlib
import json
import re
from io import StringIO
from typing import Any

import yaml
from dotenv.parser import parse_stream

from mato_loop_backend.integrations.arcane.client import ArcaneError
from mato_loop_backend.privacy.pseudonymization import _secret_results
from mato_loop_backend.security import CredentialCipher

MAX_CONFIG_CHARACTERS = 80_000
# A one-character value can expand to a marker; the 256 KiB source bound limits marker IDs to six digits.
MAX_CONFIG_INPUT_CHARACTERS = MAX_CONFIG_CHARACTERS * 32
# Covers two expanded 256 KiB representations (visible text and replacements), JSON and Fernet/Base64 overhead.
MAX_CONFIG_SNAPSHOT_CHARACTERS = 32 * 1024 * 1024
MAX_CONFIG_RESPONSE_BYTES = 2 * 1024 * 1024
_PRIVATE_MARKER = re.compile(r"__ARCANE_PRIVATE_[0-9]+__")


def configuration(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ArcaneError("integration.invalidResponse")
    result: dict[str, Any] = {}
    for field in ("name", "composeContent", "envContent", "overrideContent", "composeFileName", "overrideFileName"):
        value = data.get(field, "")
        if not isinstance(value, str) or len(value) > MAX_CONFIG_CHARACTERS:
            raise ArcaneError("integration.responseTooLarge")
        result[field] = value
    if not result["composeContent"]:
        raise ArcaneError("integration.invalidResponse")
    files = data.get("includeFiles") or []
    compose_files = data.get("composeFiles") or []
    if not isinstance(files, list) or len(files) > 40 or not isinstance(compose_files, list) or len(compose_files) > 40:
        raise ArcaneError("integration.responseTooLarge")
    result["includeFiles"] = []
    for item in files:
        if not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in ("relativePath", "content")):
            raise ArcaneError("integration.invalidResponse")
        result["includeFiles"].append({key: item[key] for key in ("relativePath", "content")})
    if not all(isinstance(item, str) and len(item) <= 256 for item in compose_files):
        raise ArcaneError("integration.invalidResponse")
    result["composeFiles"] = compose_files
    if len(json.dumps(result)) > 256 * 1024:
        raise ArcaneError("integration.responseTooLarge")
    return result


def fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def _yaml_private_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    seen: set[tuple[int, bool, bool]] = set()

    def visit(node: yaml.Node, private: bool = False, assignments: bool = False) -> None:
        identity = (id(node), private, assignments)
        if identity in seen:
            return
        seen.add(identity)
        if len(seen) > 10_000:
            raise ArcaneError("integration.responseTooLarge")
        if isinstance(node, yaml.ScalarNode):
            if private and node.tag != "tag:yaml.org,2002:null" and not re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", node.value):
                start, end = node.start_mark.index, node.end_mark.index
                # Keep environment-list keys visible; the complete raw scalar is restored on save.
                if assignments and "=" in node.value and text[start:end] == node.value:
                    start += node.value.index("=") + 1
                spans.append((start, end))
        elif isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                if not isinstance(key, yaml.ScalarNode):
                    raise ArcaneError("integration.invalidResponse")
                visit(value, private or key.value in {"environment", "args", "content"},
                      key.value in {"environment", "args"} and isinstance(value, yaml.SequenceNode))
        elif isinstance(node, yaml.SequenceNode):
            for value in node.value:
                visit(value, private, assignments)

    try:
        for root in yaml.compose_all(text, Loader=yaml.SafeLoader):
            if root is not None:
                visit(root)
    except (yaml.YAMLError, RecursionError) as exc:
        raise ArcaneError("integration.invalidResponse") from exc
    return spans


def _redact(text: str, replacements: dict[str, str], *, dotenv: bool = False) -> str:
    if _PRIVATE_MARKER.search(text):
        raise ArcaneError("integration.invalidResponse")
    # ponytail: installed secret detectors cover inline credentials; use explicit secret references for new values.
    spans = [(item.start, item.end) for item in _secret_results(text)]
    if dotenv:
        offset = 0
        for binding in parse_stream(StringIO(text)):
            raw = binding.original.string
            if binding.error:
                raise ArcaneError("integration.invalidResponse")
            if binding.value is not None:
                prefix = re.match(r"\s*(?:export\s+)?(?:'[^']+'|[^\s=]+)\s*=", raw)
                if prefix is None:
                    raise ArcaneError("integration.invalidResponse")
                start = prefix.end()
                end = len(raw.rstrip("\r\n"))
                if start < end:
                    spans.append((offset + start, offset + end))
            offset += len(raw)
    else:
        spans.extend(_yaml_private_spans(text))
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    for start, end in reversed(merged):
        marker = f"__ARCANE_PRIVATE_{len(replacements)}__"
        replacements[marker] = text[start:end]
        text = text[:start] + marker + text[end:]
    return text


def project_snapshot(config: dict[str, Any], *, integration_id: str, environment_id: str,
                     project_id: str, session_id: str, binding_revision: str) -> dict[str, Any]:
    replacements: dict[str, str] = {}
    visible = dict(config)
    for field in ("composeContent", "envContent", "overrideContent"):
        visible[field] = _redact(config[field], replacements, dotenv=field == "envContent")
    visible["includeFiles"] = [
        {"relativePath": item["relativePath"], "content": _redact(item["content"], replacements)}
        for item in config["includeFiles"]
    ]
    token = CredentialCipher().encrypt({
        "integrationId": integration_id, "environmentId": environment_id, "projectId": project_id,
        "sessionId": session_id, "bindingRevision": fingerprint({"revision": binding_revision}),
        "configurationDigest": fingerprint(config),
        "visible": {field: visible[field] for field in ("composeContent", "envContent", "overrideContent")},
        "replacements": replacements,
    }).decode("ascii")
    return {**visible, "snapshotToken": token}


def read_snapshot(token: str, *, integration_id: str, environment_id: str, project_id: str,
                  session_id: str, binding_revision: str) -> dict[str, Any]:
    try:
        snapshot = CredentialCipher().decrypt(token.encode("ascii"))
        expected = {"integrationId": integration_id, "environmentId": environment_id, "projectId": project_id,
                    "sessionId": session_id, "bindingRevision": fingerprint({"revision": binding_revision})}
        if any(snapshot.get(key) != value for key, value in expected.items()):
            raise ValueError("Arcane project snapshot is not bound")
        return snapshot
    except Exception as exc:
        raise ArcaneError("integration.staleReference") from exc


def restore_private_values(text: str, snapshot: dict[str, Any] | None) -> str:
    def restore(match: re.Match[str]) -> str:
        replacements = snapshot["replacements"] if snapshot else {}
        if match[0] not in replacements:
            raise ArcaneError("tool.invalidArguments")
        return replacements[match[0]]

    return _PRIVATE_MARKER.sub(restore, text)
