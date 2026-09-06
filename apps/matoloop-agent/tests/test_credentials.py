import hashlib
import json
import stat
import tempfile
import unittest
from pathlib import Path

import httpx

from matoloop_agent.credentials import (
    CredentialError,
    PairingPermanentError,
    PairingTemporaryError,
    derive_pairing_url,
    resolve_credential,
    select_pairing_token,
)

AGENT_ID = "2ea504cc-e3c6-4e0d-aa05-080328100fe8"
BACKEND_URL = "wss://mato.example/base/api/execution-agents/connect"


def state(base: Path) -> dict:
    return json.loads((base / "agent-state" / "credential.json").read_text())


def client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class CredentialTests(unittest.IsolatedAsyncioTestCase):
    def test_pairing_input_precedence_and_url_derivation(self) -> None:
        self.assertEqual(
            select_pairing_token(
                "cli-token",
                {
                    "MATOLOOP_AGENT_PAIRING_TOKEN": "env-token",
                },
            ),
            "cli-token",
        )
        self.assertEqual(
            select_pairing_token(
                None,
                {
                    "MATOLOOP_AGENT_PAIRING_TOKEN": "env-token",
                },
            ),
            "env-token",
        )
        self.assertEqual(
            derive_pairing_url(BACKEND_URL),
            "https://mato.example/base/api/execution-agents/pair",
        )
        self.assertEqual(
            derive_pairing_url("ws://localhost:6020/api/execution-agents/connect"),
            "http://localhost:6020/api/execution-agents/pair",
        )

    async def test_pair_persists_finalized_state_with_secure_modes_and_binding(self) -> None:
        received = {}

        def handler(request: httpx.Request) -> httpx.Response:
            received["url"] = str(request.url)
            received["authorization"] = request.headers["Authorization"]
            received["body"] = json.loads(request.content)
            return httpx.Response(204)

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            http = client(handler)
            credential = await resolve_credential(
                data_dir=base,
                backend_url=BACKEND_URL,
                agent_id=AGENT_ID,
                pairing_token="pairing-secret",
                client=http,
            )
            saved = state(base)
            self.assertEqual(received["url"], derive_pairing_url(BACKEND_URL))
            self.assertEqual(received["authorization"], "Bearer pairing-secret")
            self.assertEqual(received["body"]["agentId"], AGENT_ID)
            self.assertEqual(
                received["body"]["accessKeyHash"],
                hashlib.sha256(credential.access_key.encode()).hexdigest(),
            )
            self.assertEqual(saved["accessKey"], credential.access_key)
            self.assertEqual(credential.backend_url, BACKEND_URL)
            self.assertEqual(credential.agent_id, AGENT_ID)
            self.assertNotIn("pending", saved)
            self.assertNotIn("pairing-secret", json.dumps(saved))
            self.assertEqual(stat.S_IMODE((base / "agent-state").stat().st_mode), 0o700)
            self.assertEqual(
                stat.S_IMODE((base / "agent-state" / "credential.json").stat().st_mode), 0o600
            )
            self.assertEqual(
                stat.S_IMODE((base / "agent-state" / "credential.lock").stat().st_mode), 0o600
            )
            restored = await resolve_credential(
                data_dir=base,
                backend_url=None,
                agent_id=None,
                pairing_token=None,
                client=http,
            )
            self.assertEqual(restored, credential)
            with self.assertRaises(CredentialError):
                await resolve_credential(
                    data_dir=base,
                    backend_url="wss://other.example/api/execution-agents/connect",
                    agent_id=AGENT_ID,
                    pairing_token=None,
                    client=http,
                )
            await http.aclose()

    async def test_pending_retry_wins_and_reuses_the_persisted_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)

            def unavailable(request: httpx.Request) -> httpx.Response:
                raise httpx.ConnectError("network details", request=request)

            failed = client(unavailable)
            with self.assertRaises(PairingTemporaryError):
                await resolve_credential(
                    data_dir=base,
                    backend_url=BACKEND_URL,
                    agent_id=AGENT_ID,
                    pairing_token="first-token",
                    client=failed,
                )
            pending_key = state(base)["pending"]["accessKey"]
            await failed.aclose()

            received = {}

            def success(request: httpx.Request) -> httpx.Response:
                received["authorization"] = request.headers["Authorization"]
                received["hash"] = json.loads(request.content)["accessKeyHash"]
                return httpx.Response(204)

            retry = client(success)
            credential = await resolve_credential(
                data_dir=base,
                backend_url=BACKEND_URL,
                agent_id=AGENT_ID,
                pairing_token="ignored-new-token",
                client=retry,
            )
            self.assertEqual(credential.access_key, pending_key)
            self.assertEqual(received["authorization"], "Bearer first-token")
            self.assertEqual(received["hash"], hashlib.sha256(pending_key.encode()).hexdigest())
            await retry.aclose()

    async def test_server_failure_retains_pending_and_permanent_failure_does_not_leak(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            unavailable = client(lambda _: httpx.Response(503, text="backend-secret-details"))
            with self.assertRaises(PairingTemporaryError):
                await resolve_credential(
                    data_dir=base,
                    backend_url=BACKEND_URL,
                    agent_id=AGENT_ID,
                    pairing_token="temporary-token",
                    client=unavailable,
                )
            self.assertEqual(state(base)["pending"]["pairingToken"], "temporary-token")
            await unavailable.aclose()

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            rejected = client(lambda _: httpx.Response(401, text="backend-secret-details"))
            with self.assertRaises(PairingPermanentError) as raised:
                await resolve_credential(
                    data_dir=base,
                    backend_url=BACKEND_URL,
                    agent_id=AGENT_ID,
                    pairing_token="rejected-token",
                    client=rejected,
                )
            self.assertNotIn("rejected-token", str(raised.exception))
            self.assertNotIn("backend-secret-details", str(raised.exception))
            self.assertFalse((base / "agent-state" / "credential.json").exists())
            await rejected.aclose()

    async def test_finalized_state_is_idempotent_and_repair_promotes_only_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            paired = client(lambda _: httpx.Response(204))
            old_credential = await resolve_credential(
                data_dir=base,
                backend_url=BACKEND_URL,
                agent_id=AGENT_ID,
                pairing_token="original-token",
                client=paired,
            )
            await paired.aclose()

            unused = client(lambda _: self.fail("stale token must not pair again"))
            self.assertEqual(
                await resolve_credential(
                    data_dir=base,
                    backend_url=BACKEND_URL,
                    agent_id=AGENT_ID,
                    pairing_token="original-token",
                    client=unused,
                ),
                old_credential,
            )
            await unused.aclose()

            rejected = client(lambda _: httpx.Response(403))
            with self.assertRaises(PairingPermanentError):
                await resolve_credential(
                    data_dir=base,
                    backend_url=BACKEND_URL,
                    agent_id=AGENT_ID,
                    pairing_token="invalid-new-token",
                    client=rejected,
                )
            self.assertEqual(state(base)["accessKey"], old_credential.access_key)
            self.assertNotIn("pending", state(base))
            await rejected.aclose()

            rotated = client(lambda _: httpx.Response(204))
            new_credential = await resolve_credential(
                data_dir=base,
                backend_url=BACKEND_URL,
                agent_id=AGENT_ID,
                pairing_token="valid-new-token",
                client=rotated,
            )
            self.assertNotEqual(new_credential.access_key, old_credential.access_key)
            self.assertEqual(state(base)["accessKey"], new_credential.access_key)
            self.assertEqual(
                state(base)["lastPairingTokenHash"],
                hashlib.sha256(b"valid-new-token").hexdigest(),
            )
            await rotated.aclose()


if __name__ == "__main__":
    unittest.main()
