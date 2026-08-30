import base64
import json
import unittest

from pydantic import ValidationError

from matoloop_agent.protocol import Hello, Request, parse_inbound, validate_arguments

REQUEST_ID = "bbc46aa8-698d-4554-92c2-1cd5c6cc33a6"
AGENT_ID = "2ea504cc-e3c6-4e0d-aa05-080328100fe8"


class ProtocolTests(unittest.TestCase):
    def test_protocol_version_one_and_hello_shape_are_strict(self) -> None:
        hello = Hello(
            agentId=AGENT_ID,
            agentVersion="1.2.3",
            computerName="host",
            platform="linux",
            homeDirectory="/home/user",
            operations=["filesystem.read"],
        )
        self.assertEqual(hello.protocol_version, 1)
        with self.assertRaises(ValidationError):
            parse_inbound(
                json.dumps({"type": "welcome", "protocolVersion": 3, "agentId": AGENT_ID})
            )
        with self.assertRaises(ValidationError):
            Hello.model_validate({**hello.model_dump(by_alias=True), "roots": []})

    def test_filesystem_arguments_require_normalized_absolute_paths(self) -> None:
        request = Request.model_validate(
            {
                "type": "request",
                "id": REQUEST_ID,
                "operation": "filesystem.list",
                "arguments": {"path": "/workspace", "offset": 0, "limit": 10},
            }
        )
        self.assertEqual(validate_arguments(request).path, "/workspace")
        for path in ("workspace", "/workspace/../etc", "/workspace//src", "/workspace/."):
            request.arguments["path"] = path
            with self.subTest(path=path), self.assertRaises(ValidationError):
                validate_arguments(request)

    def test_copy_move_and_process_paths_are_strict(self) -> None:
        copy = Request.model_validate(
            {
                "type": "request",
                "id": REQUEST_ID,
                "operation": "filesystem.copy",
                "arguments": {"path": "/source", "destination": "/destination"},
            }
        )
        self.assertEqual(validate_arguments(copy).destination, "/destination")
        copy.arguments["destination"] = "relative"
        with self.assertRaises(ValidationError):
            validate_arguments(copy)

        process = Request.model_validate(
            {
                "type": "request",
                "id": REQUEST_ID,
                "operation": "process.exec",
                "arguments": {"currentDirectory": "/workspace", "program": "python"},
            }
        )
        self.assertEqual(validate_arguments(process).current_directory, "/workspace")
        process.arguments["rootId"] = AGENT_ID
        with self.assertRaises(ValidationError):
            validate_arguments(process)

    def test_git_pathspecs_remain_relative_and_openscad_source_is_absolute(self) -> None:
        git = Request.model_validate(
            {
                "type": "request",
                "id": REQUEST_ID,
                "operation": "integration.git.add",
                "arguments": {"currentDirectory": "/repo", "paths": ["src/app.py"]},
            }
        )
        self.assertEqual(validate_arguments(git).paths, ["src/app.py"])
        git.arguments["paths"] = ["/src/app.py"]
        with self.assertRaises(ValidationError):
            validate_arguments(git)

        render = Request.model_validate(
            {
                "type": "request",
                "id": REQUEST_ID,
                "operation": "integration.openscad.render",
                "arguments": {
                    "executablePath": "/usr/bin/openscad",
                    "sessionId": "session",
                    "entrypoint": "main.scad",
                    "source": {"type": "workspace", "path": "/project"},
                },
            }
        )
        self.assertEqual(validate_arguments(render).source.path, "/project")
        render.arguments["source"]["path"] = "project"
        with self.assertRaises(ValidationError):
            validate_arguments(render)

    def test_terminal_arguments_are_strict_and_bounded(self) -> None:
        def arguments(operation: str, values: dict):
            return validate_arguments(
                Request.model_validate(
                    {
                        "type": "request",
                        "id": REQUEST_ID,
                        "operation": operation,
                        "arguments": values,
                    }
                )
            )

        opened = arguments("terminal.open", {"rows": 24, "columns": 80})
        self.assertEqual((opened.rows, opened.columns), (24, 80))
        read = arguments(
            "terminal.read",
            {"terminalId": AGENT_ID, "maxBytes": 65_536, "waitSeconds": 30},
        )
        self.assertEqual(read.max_bytes, 65_536)
        written = arguments(
            "terminal.write",
            {"terminalId": AGENT_ID, "data": base64.b64encode(b"\x00\xff").decode()},
        )
        self.assertEqual(written.terminal_id, AGENT_ID)

        invalid = [
            ("terminal.open", {"rows": 0, "columns": 80}),
            ("terminal.read", {"terminalId": AGENT_ID, "maxBytes": 65_537}),
            ("terminal.read", {"terminalId": AGENT_ID, "waitSeconds": 31}),
            ("terminal.write", {"terminalId": AGENT_ID, "data": "not base64"}),
            (
                "terminal.write",
                {"terminalId": AGENT_ID, "data": base64.b64encode(b"x" * 65_537).decode()},
            ),
            ("terminal.close", {"terminalId": "not-a-uuid"}),
        ]
        for operation, values in invalid:
            with (
                self.subTest(operation=operation, values=list(values)),
                self.assertRaises(ValidationError),
            ):
                arguments(operation, values)

    def test_terminal_run_requires_exactly_one_execution_form(self) -> None:
        base = {
            "terminalId": AGENT_ID,
            "currentDirectory": "/workspace",
            "rows": 24,
            "columns": 80,
            "timeoutSeconds": 5,
        }

        command = Request.model_validate(
            {
                "type": "request",
                "id": REQUEST_ID,
                "operation": "terminal.run",
                "arguments": {**base, "command": "pwd", "expectedInspectionRevision": "a" * 64},
            }
        )
        self.assertEqual(validate_arguments(command).command, "pwd")
        program = command.model_copy(
            update={"arguments": {**base, "program": "python", "args": ["--version"]}}
        )
        self.assertEqual(validate_arguments(program).args, ["--version"])

        for values in (
            {
                **{key: value for key, value in base.items() if key != "rows"},
                "command": "pwd",
            },
            {**base, "columns": 1001, "command": "pwd"},
            {**base, "command": "pwd", "program": "python"},
            {**base, "command": "pwd", "args": []},
            {**base, "command": "pwd", "args": ["extra"]},
            {**base, "program": "python", "expectedInspectionRevision": "a" * 64},
            {**base, "program": "python", "currentDirectory": "relative"},
        ):
            command.arguments = values
            with self.subTest(values=list(values)), self.assertRaises(ValidationError):
                validate_arguments(command)


if __name__ == "__main__":
    unittest.main()
