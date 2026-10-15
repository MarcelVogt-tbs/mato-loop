from __future__ import annotations

import hashlib
import json
from typing import Any

from mato_loop_backend.integrations.arcane.client import ArcaneError
from mato_loop_backend.security import CredentialCipher

MAX_CONFIG_CHARACTERS = 80_000
MAX_CONFIG_RESPONSE_BYTES = 2 * 1024 * 1024


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


def project_snapshot(config: dict[str, Any], *, integration_id: str, environment_id: str,
                     project_id: str, session_id: str, binding_revision: str) -> dict[str, Any]:
    token = CredentialCipher().encrypt({
        "version": 2,
        "integrationId": integration_id, "environmentId": environment_id, "projectId": project_id,
        "sessionId": session_id, "bindingRevision": fingerprint({"revision": binding_revision}),
        "configurationDigest": fingerprint(config),
    }).decode("ascii")
    return {**config, "snapshotToken": token}


def read_snapshot(token: str, *, integration_id: str, environment_id: str, project_id: str,
                  session_id: str, binding_revision: str) -> dict[str, Any]:
    try:
        snapshot = CredentialCipher().decrypt(token.encode("ascii"))
        expected = {"version": 2, "integrationId": integration_id, "environmentId": environment_id, "projectId": project_id,
                    "sessionId": session_id, "bindingRevision": fingerprint({"revision": binding_revision})}
        if any(snapshot.get(key) != value for key, value in expected.items()):
            raise ValueError("Arcane project snapshot is not bound")
        return snapshot
    except Exception as exc:
        raise ArcaneError("integration.staleReference") from exc
