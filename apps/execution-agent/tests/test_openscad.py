import hashlib
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
from pydantic import ValidationError

from mato_loop_execution_agent.config import AgentConfig
from mato_loop_execution_agent.connection import ExecutionAgent
from mato_loop_execution_agent.openscad import OpenScadRunner
from mato_loop_execution_agent.protocol import ArtifactDescriptor, Request, validate_arguments
from mato_loop_execution_agent.transfer import TransferClient
from mato_loop_execution_agent.workspace import AgentError

AGENT_ID = "2ea504cc-e3c6-4e0d-aa05-080328100fe8"
ROOT_ID = "4d0a316f-5fb5-4ec5-a5c4-04aed66c5e6d"
TICKET_ID = "0a01092c-13d6-49fd-b62c-e2b98deed2bb"


def _write_fake_binary(directory: Path) -> Path:
    openscad = directory / "openscad"
    openscad.write_text(
        f"""#!{sys.executable}
import json
import pathlib
import struct
import sys
import time

pathlib.Path(__file__).with_name("calls.jsonl").open("a").write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:] == ["--version"]:
    print("OpenSCAD version 2021.01")
    raise SystemExit(0)
output = pathlib.Path(sys.argv[sys.argv.index("-o") + 1])
source = pathlib.Path(sys.argv[-1])
content = source.read_text()
if "FAIL_RENDER" in content:
    print(f"\\x1b[31mfailed {{source}}\\x1b[0m", file=sys.stderr)
    raise SystemExit(2)
if "FAIL_TOP" in content and output.suffix == ".png":
    if "--camera=0,0,0,0,0,0,140" in sys.argv:
        raise SystemExit(2)
    time.sleep(5)
if "cube(" not in content:
    raise SystemExit(2)
if output.suffix == ".png":
    output.write_bytes(b"\\x89PNG\\r\\n\\x1a\\npreview")
elif output.suffix == ".off":
    output.write_text("OFF\\n3 1 0\\n0 0 0\\n1 0 0\\n0 1 0\\n3 0 1 2\\n")
else:
    output.write_bytes(bytes(80) + struct.pack("<I", 1) + bytes(50))
print("rendered")
""",
        encoding="utf-8",
    )
    openscad.chmod(0o755)
    xvfb = directory / "xvfb-run"
    xvfb.write_text(
        f"""#!{sys.executable}
import os
import sys

arguments = sys.argv[1:]
if arguments and arguments[0] == "-a":
    arguments = arguments[1:]
os.execv(arguments[0], arguments)
""",
        encoding="utf-8",
    )
    xvfb.chmod(0o755)
    return openscad


def _configuration(
    root: Path, openscad: Path, data_dir: Path | None = None, max_bytes: int = 1024 * 1024
) -> AgentConfig:
    return AgentConfig.model_validate(
        {
            "backendUrl": "ws://backend/api/execution-agents/connect",
            "agentId": AGENT_ID,
            "name": "Agent",
            "roots": [{"id": ROOT_ID, "label": "Root", "path": root}],
            "dataDir": data_dir or root / "data",
            "allowInsecure": True,
            "openscadExecutable": openscad,
            "maxTransferBytes": max_bytes,
            "maxSessionCacheBytes": max_bytes,
        }
    )


def _request(operation: str, arguments: dict) -> Request:
    return Request.model_validate(
        {
            "type": "request",
            "id": str(uuid4()),
            "operation": operation,
            "arguments": arguments,
        }
    )


class OpenScadTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(
        shutil.which("openscad") and shutil.which("xvfb-run"),
        "packaged OpenSCAD preview is unavailable",
    )
    async def test_packaged_openscad_renders_real_preview_and_stl(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            (project / "halterung.scad").write_text("cube([2, 3, 4]);\n", encoding="utf-8")
            executable = Path(shutil.which("openscad") or "")
            config = _configuration(root, executable, max_bytes=64 * 1024 * 1024)
            agent = ExecutionAgent(config, "key")
            revision = agent.workspace.roots[ROOT_ID].revision

            try:
                rendered = await agent.dispatch(
                    _request(
                        "integration.openscad.render",
                        {
                            "rootId": ROOT_ID,
                            "rootRevision": revision,
                            "currentDirectory": os.fspath(root),
                            "executablePath": os.fspath(executable),
                            "sessionId": "session",
                            "entrypoint": "halterung.scad",
                            "source": {"type": "workspace", "path": "project"},
                            "previewImages": True,
                        },
                    ),
                    backend_id=AGENT_ID,
                )
            except AgentError as exc:
                self.fail(f"Packaged OpenSCAD render failed: {exc.code} {exc.params}")

            preview = ArtifactDescriptor.model_validate(rendered["artifact"])
            self.assertEqual(rendered["output"], "preview")
            self.assertEqual(preview.filename, "halterung.off")
            OpenScadRunner._validate_off(agent.cache._path(preview))
            self.assertEqual(
                [item["view"] for item in rendered["previews"]],
                ["isometric", "front", "right", "top"],
            )
            for item in rendered["previews"]:
                descriptor = ArtifactDescriptor.model_validate(item["artifact"])
                self.assertEqual(
                    agent.cache._path(descriptor).read_bytes()[:8],
                    b"\x89PNG\r\n\x1a\n",
                )

            exported = await agent.dispatch(
                _request(
                    "integration.openscad.render",
                    {
                        "rootId": ROOT_ID,
                        "rootRevision": revision,
                        "currentDirectory": os.fspath(root),
                        "executablePath": os.fspath(executable),
                        "sessionId": "session",
                        "entrypoint": "halterung.scad",
                        "source": {"type": "workspace", "path": "project"},
                        "previewImages": True,
                        "output": "stl",
                    },
                ),
                backend_id=AGENT_ID,
            )
            model = ArtifactDescriptor.model_validate(exported["artifact"])
            self.assertGreater(model.size, 84)
            self.assertEqual(model.filename, "halterung.stl")

    async def test_discover_test_workspace_and_archive_render_and_cache_export(self) -> None:
        uploaded = []
        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w") as archive:
            archive.writestr("main.scad", "cube([2, 2, 2]);\n")
        archive_content = archive_buffer.getvalue()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, content=archive_content)
            uploaded.append(request.content)
            return httpx.Response(204)

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "workspace"
            root.mkdir()
            data_dir = base / "data"
            binaries = base / "bin"
            binaries.mkdir()
            openscad = _write_fake_binary(binaries)
            project = root / "project"
            project.mkdir()
            (project / "halterung.scad").write_text("cube([1, 1, 1]);\n", encoding="utf-8")
            http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            transfer = TransferClient(
                max_bytes=1024 * 1024,
                backend_url="ws://backend/api/execution-agents/connect",
                access_key="key",
                client=http,
            )
            with patch.dict(os.environ, {"PATH": os.fspath(binaries)}):
                agent = ExecutionAgent(
                    _configuration(root, openscad, data_dir), "key", transfer_client=transfer
                )
            self.assertEqual(agent.openscad.temp_root.parent, Path(tempfile.gettempdir()))
            self.assertNotEqual(agent.openscad.temp_root.parent, data_dir)
            revision = agent.workspace.roots[ROOT_ID].revision
            host = {
                "rootId": ROOT_ID,
                "rootRevision": revision,
                "currentDirectory": os.fspath(root),
            }

            self.assertIn("integration.openscad.test", agent.operations)
            self.assertIn("integration.openscad.render", agent.operations)
            self.assertIn("integration.openscad.render6", agent.operations)
            discovered = await agent.dispatch(
                _request("integration.openscad.discover", {}), backend_id=AGENT_ID
            )
            self.assertTrue(discovered["available"])
            self.assertTrue(discovered["headlessPreview"])
            self.assertTrue(
                (
                    await agent.dispatch(
                        _request(
                            "integration.openscad.test",
                            {"executablePath": os.fspath(openscad)},
                        ),
                        backend_id=AGENT_ID,
                    )
                )["ok"]
            )

            with patch.object(agent.openscad, "_run", wraps=agent.openscad._run) as run:
                rendered = await agent.dispatch(
                    _request(
                        "integration.openscad.render",
                        {
                            **host,
                            "executablePath": os.fspath(openscad),
                            "sessionId": "session",
                            "entrypoint": "halterung.scad",
                            "source": {"type": "workspace", "path": "project"},
                            "previewImages": True,
                        },
                    ),
                    backend_id=AGENT_ID,
                )
            preview_runs = [
                call for call in run.await_args_list if "--imgsize=800,600" in call.args[0]
            ]
            self.assertEqual(
                [call.kwargs["resource_divisor"] for call in preview_runs], [2, 2, 2, 2]
            )
            preview = ArtifactDescriptor.model_validate(rendered["artifact"])
            self.assertEqual(rendered["output"], "preview")
            self.assertEqual(preview.filename, "halterung.off")
            self.assertIn("rendered", rendered["diagnostics"])
            self.assertFalse(rendered["diagnosticsTruncated"])
            OpenScadRunner._validate_off(agent.cache._path(preview))
            self.assertEqual(
                [item["view"] for item in rendered["previews"]],
                ["isometric", "front", "right", "top"],
            )
            legacy_cameras = {
                Path(call.args[0][call.args[0].index("-o") + 1]).stem.removeprefix(
                    "halterung-"
                ): next(value for value in call.args[0] if value.startswith("--camera="))
                for call in preview_runs
            }
            self.assertEqual(
                [legacy_cameras[item["view"]] for item in rendered["previews"]],
                [
                    "--camera=0,0,0,55,0,25,140",
                    "--camera=0,0,0,90,0,0,140",
                    "--camera=0,0,0,90,0,90,140",
                    "--camera=0,0,0,0,0,0,140",
                ],
            )

            with patch.object(agent.openscad, "_run", wraps=agent.openscad._run) as run6:
                rendered6 = await agent.dispatch(
                    _request(
                        "integration.openscad.render6",
                        {
                            **host,
                            "executablePath": os.fspath(openscad),
                            "sessionId": "session",
                            "entrypoint": "halterung.scad",
                            "source": {"type": "workspace", "path": "project"},
                            "previewImages": True,
                        },
                    ),
                    backend_id=AGENT_ID,
                )
            preview_runs6 = [
                call for call in run6.await_args_list if "--imgsize=800,600" in call.args[0]
            ]
            self.assertEqual(
                [item["view"] for item in rendered6["previews"]],
                ["front", "back", "left", "right", "top", "bottom"],
            )
            self.assertEqual(
                [
                    next(value for value in call.args[0] if value.startswith("--camera="))
                    for call in preview_runs6
                ],
                [
                    "--camera=0,0,0,90,0,0,140",
                    "--camera=0,0,0,90,0,180,140",
                    "--camera=0,0,0,90,0,270,140",
                    "--camera=0,0,0,90,0,90,140",
                    "--camera=0,0,0,0,0,0,140",
                    "--camera=0,0,0,180,0,0,140",
                ],
            )
            self.assertEqual([call.kwargs["resource_divisor"] for call in preview_runs6], [2] * 6)
            self.assertTrue(
                all(
                    {"--autocenter", "--viewall", "--projection=o", "--imgsize=800,600"}
                    <= set(call.args[0])
                    for call in preview_runs6
                )
            )
            self.assertIn("rendered", rendered6["diagnostics"])

            (project / "failed.scad").write_text("FAIL_RENDER\n", encoding="utf-8")
            with self.assertRaises(AgentError) as failure:
                await agent.dispatch(
                    _request(
                        "integration.openscad.render",
                        {
                            **host,
                            "executablePath": os.fspath(openscad),
                            "sessionId": "session",
                            "entrypoint": "failed.scad",
                            "source": {"type": "workspace", "path": "project"},
                        },
                    ),
                    backend_id=AGENT_ID,
                )
            self.assertEqual(failure.exception.code, "openscad.renderFailed")

            (project / "partial.scad").write_text(
                "cube([1, 1, 1]); // FAIL_TOP\n", encoding="utf-8"
            )
            started = time.monotonic()
            with self.assertRaises(AgentError) as partial_failure:
                await agent.dispatch(
                    _request(
                        "integration.openscad.render",
                        {
                            **host,
                            "executablePath": os.fspath(openscad),
                            "sessionId": "session",
                            "entrypoint": "partial.scad",
                            "source": {"type": "workspace", "path": "project"},
                            "previewImages": True,
                        },
                    ),
                    backend_id=AGENT_ID,
                )
            self.assertEqual(partial_failure.exception.code, "openscad.previewFailed")
            self.assertLess(time.monotonic() - started, 3)
            diagnostics = failure.exception.params["diagnostics"]
            self.assertIn("failed <render>/project/failed.scad", diagnostics)
            self.assertNotIn("\x1b", diagnostics)
            self.assertNotIn(os.fspath(data_dir), diagnostics)

            archive_descriptor = {
                "artifactId": "project-archive",
                "backendId": AGENT_ID,
                "sessionId": "session",
                "path": "projects/source.zip",
                "filename": "source.zip",
                "size": len(archive_content),
                "sha256": hashlib.sha256(archive_content).hexdigest(),
                "contentType": "application/zip",
            }
            archive_render = await agent.dispatch(
                _request(
                    "integration.openscad.render",
                    {
                        "executablePath": os.fspath(openscad),
                        "sessionId": "session",
                        "entrypoint": "main.scad",
                        "output": "stl",
                        "source": {
                            "type": "archive",
                            "artifact": archive_descriptor,
                            "transfer": {
                                "url": f"http://backend/api/workspace/transfers/{TICKET_ID}",
                                "token": "download",
                            },
                        },
                    },
                ),
                backend_id=AGENT_ID,
            )
            self.assertEqual(archive_render["artifact"]["contentType"], "model/stl")

            exported = await agent.dispatch(
                _request(
                    "cache.export",
                    {
                        "artifact": rendered["artifact"],
                        "transfer": {
                            "url": f"http://backend/api/workspace/transfers/{TICKET_ID}",
                            "token": "upload",
                        },
                    },
                ),
                backend_id=AGENT_ID,
            )
            self.assertEqual(exported["sha256"], preview.sha256)
            self.assertEqual(uploaded, [agent.cache._path(preview).read_bytes()])

            calls = [
                json.loads(line) for line in (binaries / "calls.jsonl").read_text().splitlines()
            ]
            self.assertTrue(any("--export-format=binstl" in call for call in calls))
            render_calls = [call for call in calls if "--export-format=off" in call]
            self.assertTrue(render_calls)
            image_calls = [
                call
                for call in calls
                if "--imgsize=800,600" in call and call[-1].endswith("halterung.scad")
            ]
            self.assertEqual(len(image_calls), 10)
            self.assertCountEqual(
                [
                    next(value for value in call if value.startswith("--camera="))
                    for call in image_calls
                ],
                [
                    "--camera=0,0,0,55,0,25,140",
                    "--camera=0,0,0,90,0,0,140",
                    "--camera=0,0,0,90,0,90,140",
                    "--camera=0,0,0,0,0,0,140",
                    "--camera=0,0,0,90,0,0,140",
                    "--camera=0,0,0,90,0,180,140",
                    "--camera=0,0,0,90,0,270,140",
                    "--camera=0,0,0,90,0,90,140",
                    "--camera=0,0,0,0,0,0,140",
                    "--camera=0,0,0,180,0,0,140",
                ],
            )
            self.assertTrue(
                all(
                    Path(call[call.index("-o") + 1]).is_absolute() for call in calls if "-o" in call
                )
            )
            self.assertTrue(all(Path(call[-1]).is_absolute() for call in calls if "-o" in call))
            await http.aclose()

    async def test_strict_contract_custom_validation_and_capability_omission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binaries = root / "bin"
            binaries.mkdir()
            openscad = _write_fake_binary(binaries)
            config = _configuration(root, openscad)
            with patch.object(OpenScadRunner, "_probe_render", return_value=False):
                agent = ExecutionAgent(config, "key")
            self.assertIn("integration.openscad.test", agent.operations)
            self.assertNotIn("integration.openscad.render", agent.operations)
            self.assertNotIn("integration.openscad.render6", agent.operations)
            with self.assertRaises(AgentError) as failed_probe:
                await agent.dispatch(
                    _request(
                        "integration.openscad.test",
                        {"executablePath": os.fspath(openscad)},
                    ),
                    backend_id=AGENT_ID,
                )
            self.assertEqual(failed_probe.exception.code, "openscad.headlessPreviewUnavailable")

            available = ExecutionAgent(config, "key")
            with patch.object(OpenScadRunner, "_find_openscad", return_value=None):
                unavailable = ExecutionAgent(config, "key")
            self.assertIn("integration.openscad.discover", unavailable.operations)
            self.assertIn("integration.openscad.test", unavailable.operations)
            self.assertNotIn("integration.openscad.render", unavailable.operations)
            self.assertNotIn("integration.openscad.render6", unavailable.operations)
            with self.assertRaises(AgentError) as missing_openscad:
                await unavailable.dispatch(
                    _request(
                        "integration.openscad.test",
                        {"executablePath": os.fspath(openscad)},
                    ),
                    backend_id=AGENT_ID,
                )
            self.assertEqual(missing_openscad.exception.code, "openscad.unavailable")

            request = _request(
                "integration.openscad.render",
                {
                    "rootId": ROOT_ID,
                    "rootRevision": "a" * 64,
                    "currentDirectory": os.fspath(root),
                    "executablePath": os.fspath(openscad),
                    "sessionId": "session",
                    "entrypoint": "main.scad",
                    "source": {"type": "workspace", "unexpected": True},
                },
            )
            with self.assertRaises(ValidationError):
                validate_arguments(request)

            invalid = binaries / "not-openscad"
            invalid.write_text(f"#!{sys.executable}\n", encoding="utf-8")
            invalid.chmod(0o755)
            with self.assertRaises(ValueError):
                ExecutionAgent(_configuration(root, invalid), "key")

            alternate = binaries / "alternate-openscad"
            shutil.copy(openscad, alternate)
            alternate.chmod(0o755)
            with self.assertRaises(AgentError) as untrusted:
                await available.dispatch(
                    _request(
                        "integration.openscad.test",
                        {"executablePath": os.fspath(alternate)},
                    ),
                    backend_id=AGENT_ID,
                )
            self.assertEqual(untrusted.exception.code, "openscad.invalidExecutable")

            replacement = binaries / "replacement-openscad"
            shutil.copy(openscad, replacement)
            replacement.chmod(0o755)
            os.replace(replacement, openscad)
            with self.assertRaises(AgentError) as changed:
                await available.dispatch(
                    _request(
                        "integration.openscad.test",
                        {"executablePath": os.fspath(openscad)},
                    ),
                    backend_id=AGENT_ID,
                )
            self.assertEqual(changed.exception.code, "openscad.invalidExecutable")

            values = _configuration(root, openscad).model_dump(by_alias=True)
            values["openscadExecutable"] = Path("openscad")
            with self.assertRaises(ValidationError):
                AgentConfig.model_validate(values)

            malformed_stl = root / "non-finite.stl"
            triangle = struct.pack("<12fH", *(0.0,) * 9, float("nan"), 0.0, 0.0, 0)
            malformed_stl.write_bytes(bytes(80) + struct.pack("<I", 1) + triangle)
            with self.assertRaises(AgentError):
                OpenScadRunner._validate_stl(malformed_stl)

            unsafe_archive = root / "unsafe.zip"
            with zipfile.ZipFile(unsafe_archive, "w") as archive:
                link = zipfile.ZipInfo("link.scad")
                link.create_system = 3
                link.external_attr = 0o120777 << 16
                archive.writestr(link, "main.scad")
            destination = root / "extracted"
            destination.mkdir()
            with self.assertRaises(AgentError) as unsafe:
                available.openscad._extract_archive(unsafe_archive, destination)
            self.assertEqual(unsafe.exception.code, "openscad.unsafeProject")

            traversal_archive = root / "traversal.zip"
            with zipfile.ZipFile(traversal_archive, "w") as archive:
                archive.writestr("../outside.scad", "cube(1);")
            with self.assertRaises(AgentError) as traversal:
                available.openscad._extract_archive(traversal_archive, destination)
            self.assertEqual(traversal.exception.code, "openscad.unsafeProject")


if __name__ == "__main__":
    unittest.main()
