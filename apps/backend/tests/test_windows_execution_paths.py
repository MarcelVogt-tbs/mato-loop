from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from mato_loop_backend.execution.paths import absolute_path, parent, within
from mato_loop_backend.execution.protocol import HelloMessage
from mato_loop_backend.files.access import execution_path as _execution_path
from mato_loop_backend.permissions.policy import (
    SessionGrantScope,
    execution_directory_scope,
    session_grant_matches_request,
)


def test_windows_paths_are_absolute_drive_paths_and_grants_are_case_insensitive() -> None:
    assert absolute_path("C:/Users/Test/Project") == "C:/Users/Test/Project"
    assert parent("C:/file.txt") == "C:/"
    assert within("C:/USERS/Test/file.txt", "C:/Users/test")
    assert within("C:/Users/Test/file.txt", "C:/")
    assert not within("D:/Users/test", "C:/Users/test")
    assert not within("C:/Users/testing", "C:/Users/test")
    for invalid in ("c:/Users", "C:relative", "C:/Users/../secret", "C:/Users//secret",
                    "C:/Users/file:stream", "C:/Users/CON", "//server/share", "C:\\Users"):
        with pytest.raises(ValueError):
            absolute_path(invalid)
    agent_id = uuid4()
    grant = SessionGrantScope("read", "execution_directory",
                              execution_directory_scope(agent_id, "C:/Users/Test"))
    assert session_grant_matches_request(
        "filesystem.read", "execution_directory",
        execution_directory_scope(agent_id, "C:/USERS/test/file.txt"), grant,
    )


def test_windows_hello_advertises_platform_and_native_home() -> None:
    hello = HelloMessage.model_validate({
        "type": "hello", "protocolVersion": 1, "agentId": uuid4(),
        "agentVersion": "0.1.0", "computerName": "win-test", "platform": "win32",
        "homeDirectory": "C:/Users/Test/mato-loop-agent/workspace",
        "operations": ["filesystem.read", "shell.inspect", "shell.run"],
        "systemInfo": {"operatingSystem": "Windows"},
    })
    assert hello.home_directory.startswith("C:/Users/")
    assert hello.system_info.operating_system == "Windows"
    with pytest.raises(ValueError):
        HelloMessage.model_validate({
            **hello.model_dump(by_alias=True), "platform": "linux",
        })


def test_file_tool_resolves_relative_paths_inside_windows_workspace() -> None:
    with patch("mato_loop_backend.files.access.workspace_client", return_value=SimpleNamespace(
        current_directory="C:/Users/Test/workspace"
    )):
        assert _execution_path(None, "folder/readme.txt") == "C:/Users/Test/workspace/folder/readme.txt"
        assert _execution_path(None, "C:/") == "C:/"
        assert _execution_path(None, "../shared.txt") == "C:/Users/Test/shared.txt"
