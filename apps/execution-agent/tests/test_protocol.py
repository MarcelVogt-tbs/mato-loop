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

    def test_protocol_version_is_fixed(self) -> None:
        with self.assertRaises(ValidationError):
            parse_inbound(
                json.dumps(
                    {
                        "type": "welcome",
                        "protocolVersion": 2,
                        "agentId": "2ea504cc-e3c6-4e0d-aa05-080328100fe8",
                    }
                )
            )


if __name__ == "__main__":
    unittest.main()
