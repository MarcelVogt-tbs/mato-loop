import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mato_loop_execution_agent.__main__ import _execute, main
from mato_loop_execution_agent.config import ConnectionConfig
from mato_loop_execution_agent.credentials import NotPairedError, ResolvedCredential

BACKEND_URL = "wss://mato.example/api/execution-agents/connect"
AGENT_ID = "2ea504cc-e3c6-4e0d-aa05-080328100fe8"


class CliTests(unittest.IsolatedAsyncioTestCase):
    def config(self) -> SimpleNamespace:
        return SimpleNamespace(
            data_dir=Path("/data"),
            backend_url=None,
            agent_id=None,
            allow_insecure=False,
            token=None,
        )

    async def test_pair_stores_credential_without_starting_agent(self) -> None:
        output = StringIO()
        with (
            patch(
                "mato_loop_execution_agent.__main__.resolve_credential",
                new=AsyncMock(return_value=ResolvedCredential(BACKEND_URL, AGENT_ID, "access-key")),
            ) as resolve,
            patch("mato_loop_execution_agent.__main__.ExecutionAgent") as agent,
            redirect_stdout(output),
        ):
            await _execute(
                "pair",
                self.config(),
                ConnectionConfig(backendUrl=BACKEND_URL, agentId=AGENT_ID),
                "pairing-token",
            )

        resolve.assert_awaited_once()
        agent.assert_not_called()
        self.assertEqual(output.getvalue(), "Execution agent paired successfully\n")

    async def test_run_waits_until_pair_command_stores_credential(self) -> None:
        run_forever = AsyncMock()
        with (
            patch(
                "mato_loop_execution_agent.__main__.resolve_credential",
                new=AsyncMock(
                    side_effect=[
                        NotPairedError("not paired"),
                        ResolvedCredential(BACKEND_URL, AGENT_ID, "access-key"),
                    ]
                ),
            ) as resolve,
            patch("mato_loop_execution_agent.__main__.asyncio.sleep", new=AsyncMock()) as sleep,
            patch("mato_loop_execution_agent.__main__.ExecutionAgent") as agent,
        ):
            agent.return_value.run_forever = run_forever
            await _execute("run", self.config(), None, None)

        self.assertEqual(resolve.await_count, 2)
        sleep.assert_awaited_once_with(1)
        run_forever.assert_awaited_once()
        self.assertTrue(callable(run_forever.await_args.args[0]))


class CliMainTests(unittest.TestCase):
    def test_config_argument_defaults_to_config_json(self) -> None:
        config = CliTests().config()
        with (
            patch.object(sys, "argv", ["mato-loop-execution-agent", "run"]),
            patch.dict(os.environ, {}, clear=True),
            patch(
                "mato_loop_execution_agent.__main__.AgentConfig.load",
                return_value=config,
            ) as load,
            patch("mato_loop_execution_agent.__main__._execute", new=AsyncMock()) as execute,
        ):
            main()

        load.assert_called_once_with(Path("config.json"))
        execute.assert_awaited_once_with("run", config, None, None)

    def test_pair_prompts_for_complete_connection(self) -> None:
        config = CliTests().config()
        with (
            patch.object(sys, "argv", ["mato-loop-execution-agent", "pair"]),
            patch.dict(os.environ, {}, clear=True),
            patch.object(sys.stdin, "isatty", return_value=True),
            patch("builtins.input", side_effect=[BACKEND_URL, AGENT_ID]),
            patch(
                "mato_loop_execution_agent.__main__.getpass.getpass",
                return_value="pairing-token",
            ),
            patch(
                "mato_loop_execution_agent.__main__.AgentConfig.load",
                return_value=config,
            ),
            patch("mato_loop_execution_agent.__main__._execute", new=AsyncMock()) as execute,
        ):
            main()

        execute.assert_awaited_once_with(
            "pair",
            config,
            ConnectionConfig(backendUrl=BACKEND_URL, agentId=AGENT_ID),
            "pairing-token",
        )

    def test_run_pairs_automatically_only_with_complete_environment(self) -> None:
        config = CliTests().config()
        environment = {
            "MATO_EXECUTION_AGENT_BACKEND_URL": BACKEND_URL,
            "MATO_EXECUTION_AGENT_ID": AGENT_ID,
            "MATO_EXECUTION_AGENT_PAIRING_TOKEN": "pairing-token",
        }
        with (
            patch.object(sys, "argv", ["mato-loop-execution-agent", "run"]),
            patch.dict(os.environ, environment, clear=True),
            patch(
                "mato_loop_execution_agent.__main__.AgentConfig.load",
                return_value=config,
            ),
            patch("mato_loop_execution_agent.__main__._execute", new=AsyncMock()) as execute,
        ):
            main()

        execute.assert_awaited_once_with(
            "run",
            config,
            ConnectionConfig(backendUrl=BACKEND_URL, agentId=AGENT_ID),
            "pairing-token",
        )

    def test_run_rejects_incomplete_automatic_pairing_environment(self) -> None:
        config = CliTests().config()
        environment = {
            "MATO_EXECUTION_AGENT_BACKEND_URL": BACKEND_URL,
            "MATO_EXECUTION_AGENT_PAIRING_TOKEN": "pairing-token",
        }
        with (
            patch.object(sys, "argv", ["mato-loop-execution-agent", "run"]),
            patch.dict(os.environ, environment, clear=True),
            patch(
                "mato_loop_execution_agent.__main__.AgentConfig.load",
                return_value=config,
            ),
            patch("mato_loop_execution_agent.__main__._execute", new=AsyncMock()) as execute,
            redirect_stderr(StringIO()),
            self.assertRaises(SystemExit),
        ):
            main()

        execute.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
