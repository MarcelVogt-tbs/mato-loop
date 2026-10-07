import base64
import json
import unittest

from pydantic import ValidationError

from matoloop_agent.protocol import Hello, Request, SystemInfo, parse_inbound, validate_arguments

REQUEST_ID = "bbc46aa8-698d-4554-92c2-1cd5c6cc33a6"
AGENT_ID = "2ea504cc-e3c6-4e0d-aa05-080328100fe8"


class ProtocolTests(unittest.TestCase):
    def test_maintenance_password_is_single_line(self) -> None:
        request = Request(
            type="request",
            id=REQUEST_ID,
            operation="maintenance.start",
            arguments={"action": "update", "jobId": REQUEST_ID, "sudoPassword": "secret"},
        )
        self.assertEqual(validate_arguments(request).sudo_password, "secret")
        request.arguments["sudoPassword"] = "secret\nnext"
        with self.assertRaises(ValidationError):
            validate_arguments(request)

    def test_shell_environment_is_strict_bounded_and_protects_clean_values(self) -> None:
        request = Request(
            type="request",
            id=REQUEST_ID,
            operation="shell.run",
            arguments={
                "command": "printenv CUSTOM_VALUE",
                "currentDirectory": "/workspace",
                "environment": {"CUSTOM_VALUE": "secret"},
            },
        )
        self.assertEqual(validate_arguments(request).environment, {"CUSTOM_VALUE": "secret"})
        request.arguments["environment"] = {
            **{f"NAME_{index}": "value" for index in range(31)},
            "MAX_VALUE": "x" * 65_536,
        }
        self.assertEqual(len(validate_arguments(request).environment), 32)

        invalid = (
            {"environment": [("NAME", "value")]},
            {"environment": {f"NAME_{index}": "value" for index in range(33)}},
            {"environment": {"9INVALID": "value"}},
            {"environment": {"A" * 129: "value"}},
            {"environment": {"NAME": 1}},
            {"environment": {"NAME": "x" * 65_537}},
            {"environment": {"NAME": "\N{EURO SIGN}" * 21_846}},
            {"environment": {"NAME": "before\x00after"}},
            *(
                {"environment": {name: "replacement"}}
                for name in (
                    "HOME",
                    "PATH",
                    "BASH_ENV",
                    "LD_PRELOAD",
                    "GIT_CONFIG_COUNT",
                    "DYLD_INSERT_LIBRARIES",
                )
            ),
        )
        for arguments in invalid:
            request.arguments = {
                "command": "true",
                "currentDirectory": "/workspace",
                **arguments,
            }
            with self.subTest(arguments=arguments), self.assertRaises(ValidationError):
                validate_arguments(request)

        request.operation = "administrator.run"
        request.arguments = {
            "operation": "shell.run",
            "arguments": {
                "command": "printenv CUSTOM_VALUE",
                "currentDirectory": "/workspace",
                "environment": {"CUSTOM_VALUE": "secret"},
            },
        }
        self.assertEqual(
            validate_arguments(request).arguments["environment"], {"CUSTOM_VALUE": "secret"}
        )
        request.arguments["arguments"]["environment"] = {"BASH_ENV": "unsafe"}
        with self.assertRaises(ValidationError):
            validate_arguments(request)

        request.operation = "terminal.run"
        request.arguments = {
            "terminalId": AGENT_ID,
            "currentDirectory": "/workspace",
            "rows": 24,
            "columns": 80,
            "command": "true",
            "environment": {"NAME": "value"},
        }
        with self.assertRaises(ValidationError):
            validate_arguments(request)

    def test_shell_and_terminal_timeouts_are_optional_and_bounded(self) -> None:
        for operation in ("shell.run", "terminal.run"):
            values = {"command": "pwd", "currentDirectory": "/workspace"}
            if operation == "terminal.run":
                values.update(terminalId=AGENT_ID, rows=24, columns=80)
            request = Request(type="request", id=REQUEST_ID, operation=operation, arguments=values)
            self.assertIsNone(validate_arguments(request).timeout_seconds)
            for timeout in (None, 0.01, 3600):
                request.arguments["timeoutSeconds"] = timeout
                self.assertEqual(validate_arguments(request).timeout_seconds, timeout)
            for timeout in (0, -1, 3600.1, float("inf"), float("nan"), True, "60"):
                request.arguments["timeoutSeconds"] = timeout
                with self.subTest(operation=operation, timeout=timeout):
                    with self.assertRaises(ValidationError):
                        validate_arguments(request)

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
        self.assertIsNone(hello.system_info)
        self.assertNotIn("systemInfo", hello.model_dump(by_alias=True, exclude_none=True))
        with self.assertRaises(ValidationError):
            parse_inbound(
                json.dumps({"type": "welcome", "protocolVersion": 3, "agentId": AGENT_ID})
            )
        with self.assertRaises(ValidationError):
            Hello.model_validate({**hello.model_dump(by_alias=True), "roots": []})

    def test_system_info_fields_are_nullable_strict_and_bounded(self) -> None:
        self.assertTrue(all(value is None for value in SystemInfo().model_dump().values()))
        for field, maximum in (
            ("operatingSystem", 80),
            ("distribution", 255),
            ("kernel", 160),
            ("architecture", 80),
            ("cpuModel", 255),
            ("logicalCpuCount", 2147483647),
            ("totalMemoryBytes", 9223372036854775807),
        ):
            numeric = field in {"logicalCpuCount", "totalMemoryBytes"}
            valid = (None, 1, maximum) if numeric else (None, "x", "x" * maximum)
            invalid = (
                (0, -1, maximum + 1, True, 1.0, "1")
                if numeric
                else ("", "x" * (maximum + 1), 1, True, b"Linux")
            )
            for value in valid:
                with self.subTest(field=field, value=value):
                    info = SystemInfo.model_validate({field: value})
                    self.assertEqual(info.model_dump(by_alias=True)[field], value)
            for value in invalid:
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    SystemInfo.model_validate({field: value})
        with self.assertRaises(ValidationError):
            SystemInfo.model_validate({"memory": {"availableBytes": 1024}})

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

    def test_copy_move_paths_are_strict_and_process_exec_is_unsupported(self) -> None:
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
        with self.assertRaises(ValueError, msg="unsupported operation"):
            validate_arguments(process)

    def test_patch_requires_absolute_revision_paths_and_bounded_strict_fields(self) -> None:
        request = Request.model_validate(
            {
                "type": "request",
                "id": REQUEST_ID,
                "operation": "filesystem.patch",
                "arguments": {
                    "patch": "*** Begin Patch\n*** Delete File: /workspace/old\n*** End Patch\n",
                    "expectedSha256": {"/workspace/old": "a" * 64},
                },
            }
        )
        arguments = validate_arguments(request)
        self.assertEqual(arguments.expected_sha256["/workspace/old"], "a" * 64)
        for revisions in (
            {"workspace/old": "a" * 64},
            {"/workspace/../old": "a" * 64},
            {"/workspace/old": "A" * 64},
        ):
            request.arguments["expectedSha256"] = revisions
            with self.subTest(revisions=revisions), self.assertRaises(ValidationError):
                validate_arguments(request)

        request.arguments = {"patch": "x" * (512 * 1024 + 1), "expectedSha256": {}}
        with self.assertRaises(ValidationError):
            validate_arguments(request)

    def test_openscad_source_is_absolute(self) -> None:
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
        self.assertIsNone(opened.terminal_id)
        self.assertEqual(
            arguments(
                "terminal.open", {"rows": 24, "columns": 80, "terminalId": AGENT_ID}
            ).terminal_id,
            AGENT_ID,
        )
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
            ("terminal.open", {"rows": 24, "columns": 80, "terminalId": "invalid"}),
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

    def test_terminal_run_requires_a_shell_command(self) -> None:
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
        for values in (
            {
                **{key: value for key, value in base.items() if key != "rows"},
                "command": "pwd",
            },
            {**base, "columns": 1001, "command": "pwd"},
            {**base, "command": "pwd", "program": "python"},
            {**base, "command": "pwd", "args": []},
            {**base, "currentDirectory": "relative", "command": "pwd"},
            base,
        ):
            command.arguments = values
            with self.subTest(values=list(values)), self.assertRaises(ValidationError):
                validate_arguments(command)


if __name__ == "__main__":
    unittest.main()
