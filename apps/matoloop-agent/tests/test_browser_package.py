"""Opt-in native package/setup smoke; run only in a disposable dev agent container.

Pass the archive built by scripts/build-matoloop-agent-artifact.sh. This exercises
the real native apt/setup branch and downloads Chromium into disposable storage.
It intentionally does not run during ordinary test discovery.
"""

from __future__ import annotations

import asyncio
import json
import os
import tarfile
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from matoloop_agent.browser import BrowserManager
from matoloop_agent.browser_contracts import BrowserOpenArguments, BrowserStatusArguments


async def smoke(archive: Path) -> None:
    assert os.getuid() == 0, "Use a disposable development container, not the host"
    with tempfile.TemporaryDirectory(prefix="browser-package-smoke-") as directory:
        base = Path(directory)
        base.chmod(0o755)
        with tarfile.open(archive) as package:
            package.extractall(base, filter="data")
        binary = next(base.glob("matoloop-agent-linux-amd64-*/matoloop-agent"))
        script = binary.with_name("browser-setup.sh")
        data = base / "data"
        data.mkdir(mode=0o755)
        config = SimpleNamespace(
            data_dir=data,
            backend_url="ws://package.test/api/execution-agents/connect",
            agent_id=str(uuid4()),
        )
        with patch.dict(os.environ, {"MATOLOOP_AGENT_CONTAINER": "0"}):
            manager = BrowserManager(config, None)
        root = manager.storage(str(uuid4()))
        environment = {
            "PATH": "/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin",
            "LANG": "C.UTF-8",
            "MATOLOOP_AGENT_CONTAINER": "0",
            "MATOLOOP_BROWSER_EXECUTABLE": str(binary),
            "MATOLOOP_BROWSER_FROZEN": "1",
            "MATOLOOP_BROWSER_DATA_DIR": str(data),
            "MATOLOOP_BROWSER_ROOT": str(root),
            "MATOLOOP_BROWSER_INSTALLATION": str(manager.installation),
        }
        print("Running packaged native setup in disposable storage", flush=True)
        process = await asyncio.create_subprocess_exec(
            "/bin/bash",
            str(script),
            env=environment,
            cwd="/",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await process.communicate()
        if process.returncode:
            print(output.decode(errors="replace"))
        assert process.returncode == 0, "Packaged setup failed"
        assert manager.installation.is_dir()
        with patch(
            "matoloop_agent.browser.browser_command", lambda command: [str(binary), command]
        ):
            try:
                status = await manager.dispatch(
                    "integration.browser.status", BrowserStatusArguments()
                )
                assert status["state"] == "ready", status
                browser_id = str(uuid4())
                await manager.dispatch(
                    "integration.browser.open",
                    BrowserOpenArguments(
                        ownerId=manager.owner_id,
                        browserId=browser_id,
                    ),
                )
                result = await manager.rpc(
                    "execute", args={"browserId": browser_id, "action": "tabs"}
                )
                assert result["tabs"]
                print("PACKAGED BROWSER PASS: " + json.dumps(status), flush=True)
            finally:
                await manager.close()


if __name__ == "__main__":
    import sys

    asyncio.run(smoke(Path(sys.argv[1])))
