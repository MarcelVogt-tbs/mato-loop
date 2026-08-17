import json
import unittest

from pydantic import ValidationError

from mato_loop_execution_agent.protocol import Request, parse_inbound, validate_arguments

REQUEST_ID = "bbc46aa8-698d-4554-92c2-1cd5c6cc33a6"


class ProtocolTests(unittest.TestCase):
    def test_envelope_and_arguments_reject_unknown_fields_and_coercion(self) -> None:
        request = Request.model_validate(
            {
                "type": "request",
                "id": REQUEST_ID,
                "operation": "workspace.list",
                "arguments": {
                    "rootId": "4d0a316f-5fb5-4ec5-a5c4-04aed66c5e6d",
                    "rootRevision": "a" * 64,
                    "workingDirectory": ".",
                    "path": ".",
                    "offset": 0,
                    "limit": 10,
                },
            }
        )
        self.assertEqual(validate_arguments(request).limit, 10)

        with self.assertRaises(ValidationError):
            parse_inbound(
                json.dumps(
                    {
                        "type": "cancel",
                        "id": REQUEST_ID,
                        "unexpected": True,
                    }
                )
            )
        request.arguments["limit"] = "10"
        with self.assertRaises(ValidationError):
            validate_arguments(request)

        shell = Request.model_validate(
            {
                "type": "request",
                "id": REQUEST_ID,
                "operation": "shell.run",
                "arguments": {
                    "rootId": "4d0a316f-5fb5-4ec5-a5c4-04aed66c5e6d",
                    "rootRevision": "a" * 64,
                    "currentDirectory": "/workspace",
                    "command": "pwd",
                    "expectedInspectionRevision": "b" * 64,
                },
            }
        )
        self.assertEqual(validate_arguments(shell).expected_inspection_revision, "b" * 64)

    def test_protocol_version_is_fixed(self) -> None:
        with self.assertRaises(ValidationError):
            parse_inbound(
                json.dumps(
                    {
                        "type": "welcome",
                        "protocolVersion": 1,
                        "agentId": "2ea504cc-e3c6-4e0d-aa05-080328100fe8",
                    }
                )
            )

    def test_host_directory_operations_require_absolute_strict_arguments(self) -> None:
        request = Request.model_validate(
            {
                "type": "request",
                "id": REQUEST_ID,
                "operation": "process.exec",
                "arguments": {
                    "rootId": "4d0a316f-5fb5-4ec5-a5c4-04aed66c5e6d",
                    "rootRevision": "a" * 64,
                    "currentDirectory": "/workspace",
                    "program": "python",
                },
            }
        )
        self.assertEqual(validate_arguments(request).current_directory, "/workspace")
        request.arguments["currentDirectory"] = "."
        with self.assertRaises(ValidationError):
            validate_arguments(request)

    def test_search_arguments_are_strict(self) -> None:
        base = {
            "type": "request",
            "id": REQUEST_ID,
            "arguments": {
                "rootId": "4d0a316f-5fb5-4ec5-a5c4-04aed66c5e6d",
                "rootRevision": "a" * 64,
                "workingDirectory": ".",
                "path": ".",
                "pattern": "*.py",
            },
        }
        glob = Request.model_validate({**base, "operation": "workspace.glob"})
        self.assertEqual(validate_arguments(glob).pattern, "*.py")

        grep = Request.model_validate(
            {
                **base,
                "operation": "workspace.grep",
                "arguments": {**base["arguments"], "include": "*.py"},
            }
        )
        self.assertEqual(validate_arguments(grep).include, "*.py")
        grep.arguments["include"] = 1
        with self.assertRaises(ValidationError):
            validate_arguments(grep)
        glob.arguments["limit"] = 10
        with self.assertRaises(ValidationError):
            validate_arguments(glob)

    def test_openscad_archive_is_rootless_and_workspace_requires_a_root(self) -> None:
        base = {
            "type": "request",
            "id": REQUEST_ID,
            "operation": "integration.openscad.render",
            "arguments": {
                "executablePath": "/usr/bin/openscad",
                "sessionId": "session",
                "entrypoint": "main.scad",
            },
        }
        archive = Request.model_validate(
            {
                **base,
                "arguments": {
                    **base["arguments"],
                    "source": {
                        "type": "archive",
                        "artifact": {
                            "artifactId": "project",
                            "backendId": "backend",
                            "sessionId": "session",
                            "path": "project.zip",
                            "filename": "project.zip",
                            "size": 1,
                            "sha256": "a" * 64,
                            "contentType": "application/zip",
                        },
                        "transfer": {"url": "https://backend/project", "token": "token"},
                    },
                },
            }
        )
        arguments = validate_arguments(archive)
        self.assertIsNone(arguments.root_id)
        self.assertFalse(arguments.preview_images)
        archive.arguments["previewImages"] = True
        self.assertTrue(validate_arguments(archive).preview_images)

        workspace = Request.model_validate(
            {
                **base,
                "arguments": {
                    **base["arguments"],
                    "source": {"type": "workspace", "path": "."},
                },
            }
        )
        with self.assertRaises(ValidationError):
            validate_arguments(workspace)

        render6 = Request.model_validate(
            {
                **base,
                "operation": "integration.openscad.render6",
                "arguments": archive.arguments.copy(),
            }
        )
        self.assertEqual(
            type(validate_arguments(render6)),
            type(validate_arguments(archive)),
        )
        render6.arguments["unexpected"] = True
        with self.assertRaises(ValidationError):
            validate_arguments(render6)


if __name__ == "__main__":
    unittest.main()
