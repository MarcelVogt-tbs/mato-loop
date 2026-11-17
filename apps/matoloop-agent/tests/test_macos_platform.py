"""Verify macOS composition and installer dispatch without host mutation."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from matoloop_agent.config import AgentConfig
from matoloop_agent.errors import AgentError
from matoloop_agent.platform import current_platform
from matoloop_agent.platform_macos import MacOSPlatform


class MacOSPlatformTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config = AgentConfig(homeDirectory=self.root, shellExecutable=Path("/bin/bash"))
        self.config._data_directory = self.root / "data"
        self.platform = MacOSPlatform()

    def test_runtime_reuses_posix_operations_without_linux_only_capabilities(self):
        with (
            patch("sys.platform", "darwin"),
            patch("platform.machine", return_value="arm64"),
            patch("os.geteuid", return_value=501),
        ):
            runtime = current_platform().build_runtime(self.config, "test-key")
        self.assertEqual(
            (
                runtime.platform.platform_id,
                "filesystem.write" in runtime.operations,
                "shell.run" in runtime.operations,
                "terminal.open" in runtime.operations,
                "integration.browser.status" in runtime.operations,
                "administrator.run" in runtime.operations,
                "maintenance.start" in runtime.operations,
            ),
            ("darwin", True, True, True, True, False, False),
        )

    async def test_maintenance_dispatches_only_the_fixed_installer_action(self):
        child = SimpleNamespace(returncode=0, wait=AsyncMock(return_value=0))
        with (
            patch.object(self.platform, "_maintenance_available", return_value=True),
            patch("asyncio.create_subprocess_exec", return_value=child) as launch,
        ):
            result = await self.platform.start_maintenance(
                self.config, "update", "8be51e93-609f-49a5-bb14-937ce4e4fc36", None, "a" * 40
            )
        self.assertEqual(
            (result, launch.call_args.args[2]), ({"started": True}, "maintenance-start")
        )

    async def test_failed_installer_launch_is_not_reported_as_started(self):
        child = SimpleNamespace(returncode=1, wait=AsyncMock(return_value=1))
        with (
            patch.object(self.platform, "_maintenance_available", return_value=True),
            patch("asyncio.create_subprocess_exec", return_value=child),
        ):
            with self.assertRaises(AgentError):
                await self.platform.start_maintenance(
                    self.config, "uninstall", "8be51e93-609f-49a5-bb14-937ce4e4fc36", None
                )
