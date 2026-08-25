from __future__ import annotations

import asyncio
import hashlib
import math
import os
import re
import resource
import shutil
import signal
import stat
import struct
import subprocess
import tempfile
import threading
import time
import weakref
import zipfile
from pathlib import Path
from uuid import uuid4

from .cache import SessionCache
from .config import AgentConfig
from .filesystem import AgentError, Filesystem, _parts, _relative_parts
from .process import ProcessRunner
from .protocol import (
    ArtifactDescriptor,
    OpenScadArchiveSource,
    OpenScadRenderArguments,
)
from .transfer import TransferClient

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_ADDRESS_SPACE_BYTES = 8 * 1024 * 1024 * 1024
MAX_CPU_SECONDS = 600
MAX_PROCESSES = 256
ANSI_ESCAPE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
CAMERAS = {
    "isometric": "0,0,0,55,0,25,140",
    "front": "0,0,0,90,0,0,140",
    "right": "0,0,0,90,0,90,140",
    "top": "0,0,0,0,0,0,140",
}
CAMERAS_6 = {
    "front": "0,0,0,90,0,0,140",
    "back": "0,0,0,90,0,180,140",
    "left": "0,0,0,90,0,270,140",
    "right": "0,0,0,90,0,90,140",
    "top": "0,0,0,0,0,0,140",
    "bottom": "0,0,0,180,0,0,140",
}
KNOWN_OPENSCAD = (Path("/usr/local/bin/openscad"), Path("/usr/bin/openscad"))


class OpenScadRunner:
    def __init__(
        self,
        config: AgentConfig,
        filesystem: Filesystem,
        cache: SessionCache,
        transfer: TransferClient,
    ) -> None:
        self.filesystem = filesystem
        self.cache = cache
        self.transfer = transfer
        self.timeout = config.default_timeout_seconds
        self.max_entries = config.max_operation_entries
        self.max_file_bytes = config.max_file_bytes
        self.max_bytes = config.max_transfer_bytes
        self.max_artifact_bytes = min(MAX_ARTIFACT_BYTES, self.max_bytes)
        self.max_diagnostics = min(config.max_output_bytes, 64 * 1024)
        self.temp_root = Path(tempfile.mkdtemp(prefix="matoloop-agent-openscad-"))
        self._temp_cleanup = weakref.finalize(
            self, shutil.rmtree, self.temp_root, ignore_errors=True
        )

        found = self._find_openscad(config.openscad_executable)
        if found is None:
            self.executable = None
            self.version = None
            self.executable_identity = None
        else:
            self.executable, self.version, self.executable_identity = found
        self.manifold_available = self._probe_manifold(self.executable)
        self.xvfb = self._find_xvfb()
        self.xvfb_identity = self._identity(self.xvfb)
        self.render_probe_available = self._probe_render()

    @property
    def render_available(self) -> bool:
        return self.executable is not None and self._headless_available()

    @staticmethod
    def _candidate(path: Path) -> Path:
        return ProcessRunner._resolve_executable(path)

    @staticmethod
    def _identity(path: Path | None) -> tuple[int, int] | None:
        if path is None:
            return None
        metadata = path.stat(follow_symlinks=False)
        return metadata.st_dev, metadata.st_ino

    def _probe_candidate(self, path: Path) -> tuple[Path, str, tuple[int, int]]:
        executable = self._candidate(path)
        identity = self._identity(executable)
        assert identity is not None
        version = self._probe_sync(executable)
        if self._identity(executable) != identity:
            raise AgentError("openscad.invalidExecutable", "OpenSCAD changed during validation")
        return executable, version, identity

    def _find_openscad(self, configured: Path | None) -> tuple[Path, str, tuple[int, int]] | None:
        if configured is not None:
            try:
                return self._probe_candidate(configured)
            except (AgentError, OSError) as exc:
                raise ValueError("configured OpenSCAD executable is unavailable") from exc

        path_candidate = shutil.which("openscad")
        candidates = list(KNOWN_OPENSCAD)
        if path_candidate:
            candidates.append(Path(path_candidate))
        seen: set[Path] = set()
        for candidate in candidates:
            try:
                executable = self._candidate(candidate)
                if executable in seen:
                    continue
                seen.add(executable)
                return self._probe_candidate(executable)
            except (AgentError, OSError):
                continue
        return None

    def _find_xvfb(self) -> Path | None:
        path_candidate = shutil.which("xvfb-run")
        candidates = [Path("/usr/bin/xvfb-run"), Path("/usr/local/bin/xvfb-run")]
        if path_candidate:
            candidates.append(Path(path_candidate))
        for candidate in candidates:
            try:
                return self._candidate(candidate)
            except AgentError:
                continue
        return None

    def _same_identity(self, path: Path | None, identity: tuple[int, int] | None) -> bool:
        if path is None or identity is None:
            return False
        try:
            return self._candidate(path) == path and self._identity(path) == identity
        except (AgentError, OSError):
            return False

    def _headless_available(self) -> bool:
        return self.render_probe_available and self._runtime_available()

    def _runtime_available(self) -> bool:
        return self._same_identity(
            self.executable, self.executable_identity
        ) and self._same_identity(self.xvfb, self.xvfb_identity)

    @staticmethod
    def _is_off(path: Path) -> bool:
        try:
            return path.read_text(encoding="utf-8").split(maxsplit=1)[0] == "OFF"
        except (OSError, UnicodeError, IndexError):
            return False

    def _probe_render(self) -> bool:
        if not self._runtime_available():
            return False
        with tempfile.TemporaryDirectory(prefix="probe-", dir=self.temp_root) as temporary:
            work = Path(temporary)
            project = work / "project"
            output = work / "output"
            project.mkdir(mode=0o700)
            output.mkdir(mode=0o700)
            source = project / "probe.scad"
            model = output / "probe.off"
            preview = output / "probe.png"
            source.write_text("cube(1);\n", encoding="ascii")
            try:
                geometry = subprocess.run(
                    [
                        os.fspath(self.executable),
                        "--export-format=off",
                        "-o",
                        os.fspath(model),
                        os.fspath(source),
                    ],
                    cwd=work,
                    env=self._environment(work),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
                result = subprocess.run(
                    [
                        os.fspath(self.xvfb),
                        "-a",
                        os.fspath(self.executable),
                        "-o",
                        os.fspath(preview),
                        "--imgsize=32,32",
                        os.fspath(source),
                    ],
                    cwd=work,
                    env=self._environment(work),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
                return (
                    geometry.returncode == 0
                    and self._is_off(model)
                    and result.returncode == 0
                    and preview.is_file()
                    and preview.read_bytes()[: len(PNG_SIGNATURE)] == PNG_SIGNATURE
                )
            except (OSError, subprocess.TimeoutExpired):
                return False

    def _probe_manifold(self, executable: Path | None) -> bool:
        if executable is None:
            return False
        with tempfile.TemporaryDirectory(prefix="manifold-", dir=self.temp_root) as temporary:
            work = Path(temporary)
            source = work / "probe.scad"
            output = work / "probe.off"
            source.write_text("cube(1);\n", encoding="ascii")
            try:
                result = subprocess.run(
                    [
                        os.fspath(executable),
                        "--backend=manifold",
                        "--export-format=off",
                        "-o",
                        os.fspath(output),
                        os.fspath(source),
                    ],
                    cwd=work,
                    env=self._environment(work),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
                return result.returncode == 0 and self._is_off(output)
            except (OSError, subprocess.TimeoutExpired):
                return False

    def _environment(self, directory: Path) -> dict[str, str]:
        home = directory / "home"
        runtime = directory / "runtime"
        cache = directory / "cache"
        config = directory / "config"
        for path in (home, runtime, cache, config):
            path.mkdir(mode=0o700, exist_ok=True)
            path.chmod(0o700)
        return {
            "HOME": os.fspath(home),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "TMPDIR": os.fspath(directory),
            "XDG_CACHE_HOME": os.fspath(cache),
            "XDG_CONFIG_HOME": os.fspath(config),
            "XDG_RUNTIME_DIR": os.fspath(runtime),
        }

    @staticmethod
    def _version(output: bytes) -> str:
        text = output.decode("utf-8", errors="replace")
        line = next((line.strip() for line in text.splitlines() if "OpenSCAD" in line), None)
        if line is None:
            raise AgentError("openscad.invalidExecutable", "Executable is not OpenSCAD")
        return line[:500]

    def _probe_sync(self, executable: Path) -> str:
        output = bytearray()
        lock = threading.Lock()
        with tempfile.TemporaryDirectory(prefix="probe-", dir=self.temp_root) as temporary:
            try:
                process = subprocess.Popen(
                    [os.fspath(executable), "--version"],
                    cwd=temporary,
                    env=self._environment(Path(temporary)),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
            except OSError as exc:
                raise AgentError("openscad.invalidExecutable", "OpenSCAD could not start") from exc

            def drain(stream) -> None:
                while chunk := stream.read(16 * 1024):
                    with lock:
                        remaining = self.max_diagnostics - len(output)
                        output.extend(chunk[: max(0, remaining)])

            threads = [
                threading.Thread(target=drain, args=(process.stdout,)),
                threading.Thread(target=drain, args=(process.stderr,)),
            ]
            for thread in threads:
                thread.start()
            try:
                process.wait(timeout=min(self.timeout, 10))
            except subprocess.TimeoutExpired as exc:
                self._terminate_sync(process)
                raise AgentError(
                    "openscad.invalidExecutable", "OpenSCAD version timed out"
                ) from exc
            finally:
                for thread in threads:
                    thread.join(timeout=2)
                process.stdout.close()
                process.stderr.close()
            if process.returncode != 0:
                raise AgentError("openscad.invalidExecutable", "OpenSCAD version failed")
        return self._version(bytes(output))

    @staticmethod
    def _terminate_sync(process: subprocess.Popen) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=2)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()

    def _sanitize_diagnostic(self, data: bytes, directory: Path) -> tuple[str, bool]:
        text = ANSI_ESCAPE.sub("", data.decode("utf-8", errors="replace"))
        for path, replacement in (
            (os.fspath(directory), "<render>"),
            (os.fspath(self.temp_root), "<temporary>"),
        ):
            text = text.replace(path, replacement)
        text = "".join(
            character
            for character in text
            if character in "\n\t" or 32 <= ord(character) < 127 or ord(character) >= 160
        )
        truncated = len(text) > self.max_diagnostics
        return text[: self.max_diagnostics], truncated

    def _process_result(
        self,
        captured: dict[str, bytearray],
        truncated: dict[str, bool],
        directory: Path,
        started: float,
        return_code: int,
    ) -> dict:
        stdout, stdout_sanitized = self._sanitize_diagnostic(bytes(captured["stdout"]), directory)
        stderr, stderr_sanitized = self._sanitize_diagnostic(bytes(captured["stderr"]), directory)
        return {
            "exitCode": return_code,
            "durationMs": round((time.monotonic() - started) * 1000),
            "stdout": stdout,
            "stderr": stderr,
            "stdoutTruncated": truncated["stdout"] or stdout_sanitized,
            "stderrTruncated": truncated["stderr"] or stderr_sanitized,
        }

    @staticmethod
    def _failure_params(result: dict) -> dict[str, str | bool]:
        return {
            "diagnostics": "\n".join(text for text in (result["stdout"], result["stderr"]) if text),
            "diagnosticsTruncated": result["stdoutTruncated"] or result["stderrTruncated"],
        }

    async def _run(
        self,
        argv: list[str],
        directory: Path,
        *,
        diagnostic_limit: int,
        error_code: str,
        timeout_code: str | None = None,
        resource_divisor: int = 1,
    ) -> dict:
        def set_limits() -> None:
            for kind, maximum in (
                (resource.RLIMIT_CPU, MAX_CPU_SECONDS // resource_divisor),
                (resource.RLIMIT_AS, MAX_ADDRESS_SPACE_BYTES // resource_divisor),
                (resource.RLIMIT_NPROC, MAX_PROCESSES // resource_divisor),
                (resource.RLIMIT_FSIZE, self.max_artifact_bytes),
            ):
                _, hard = resource.getrlimit(kind)
                limit = maximum if hard == resource.RLIM_INFINITY else min(maximum, hard)
                resource.setrlimit(kind, (limit, limit))

        started = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=directory,
                env=self._environment(directory),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                preexec_fn=set_limits,
            )
        except OSError as exc:
            raise AgentError(error_code, "OpenSCAD could not be started") from exc

        captured = {"stdout": bytearray(), "stderr": bytearray()}
        truncated = {"stdout": False, "stderr": False}
        accepted = 0

        async def drain(name: str, reader: asyncio.StreamReader) -> None:
            nonlocal accepted
            while chunk := await reader.read(16 * 1024):
                remaining = max(0, diagnostic_limit - accepted)
                captured[name].extend(chunk[:remaining])
                accepted += min(len(chunk), remaining)
                if len(chunk) > remaining:
                    truncated[name] = True

        tasks = [
            asyncio.create_task(drain("stdout", process.stdout)),
            asyncio.create_task(drain("stderr", process.stderr)),
        ]
        try:
            async with asyncio.timeout(self.timeout):
                await process.wait()
                await asyncio.gather(*tasks)
        except TimeoutError as exc:
            await ProcessRunner._terminate(process)
            await ProcessRunner._finish_drains(process, tasks)
            result = self._process_result(
                captured, truncated, directory, started, process.returncode or -1
            )
            raise AgentError(
                timeout_code or error_code,
                "OpenSCAD operation timed out",
                self._failure_params(result),
            ) from exc
        except asyncio.CancelledError:
            await ProcessRunner._terminate(process)
            await ProcessRunner._finish_drains(process, tasks)
            raise
        except BaseException:
            await ProcessRunner._terminate(process)
            await ProcessRunner._finish_drains(process, tasks)
            raise
        result = self._process_result(captured, truncated, directory, started, process.returncode)
        if process.returncode != 0:
            raise AgentError(
                error_code,
                "OpenSCAD operation failed",
                self._failure_params(result),
            )
        return result

    def discover(self) -> dict:
        available = self._same_identity(self.executable, self.executable_identity)
        return {
            "available": available,
            "executablePath": os.fspath(self.executable) if self.executable is not None else None,
            "version": self.version,
            "headlessPreview": self._headless_available(),
        }

    async def test(self, executable_path: str) -> dict:
        executable, version = await self._validated_executable(executable_path)
        return {
            "ok": True,
            "executablePath": os.fspath(executable),
            "version": version,
            "headlessPreview": True,
        }

    async def _validated_executable(self, executable_path: str) -> tuple[Path, str]:
        if self.executable is None or self.executable_identity is None:
            raise AgentError("openscad.unavailable", "OpenSCAD was not found at startup")
        path = Path(executable_path)
        if not path.is_absolute():
            raise AgentError("openscad.invalidExecutable", "OpenSCAD path must be absolute")
        try:
            executable = self._candidate(path)
        except AgentError as exc:
            raise AgentError("openscad.invalidExecutable", "OpenSCAD is unavailable") from exc
        try:
            identity = self._identity(executable)
        except OSError as exc:
            raise AgentError("openscad.invalidExecutable", "OpenSCAD is unavailable") from exc
        if executable != self.executable or identity != self.executable_identity:
            raise AgentError(
                "openscad.invalidExecutable", "OpenSCAD executable does not match startup discovery"
            )
        if not self._headless_available():
            raise AgentError(
                "openscad.headlessPreviewUnavailable", "Headless rendering is unavailable"
            )
        with tempfile.TemporaryDirectory(prefix="test-", dir=self.temp_root) as temporary:
            try:
                diagnostic = await self._run(
                    [os.fspath(executable), "--version"],
                    Path(temporary),
                    diagnostic_limit=self.max_diagnostics,
                    error_code="openscad.versionFailed",
                )
                version = self._version(
                    (diagnostic["stdout"] + "\n" + diagnostic["stderr"]).encode()
                )
            except AgentError as exc:
                raise AgentError(
                    "openscad.invalidExecutable", "Executable is not a usable OpenSCAD"
                ) from exc
        return executable, version

    def _copy_workspace_project(
        self, arguments: OpenScadRenderArguments, destination: Path
    ) -> None:
        resolved = self.filesystem.resolve_directory(arguments.source.path)
        parts = _parts(resolved["path"])
        with self.filesystem._root_fd() as root_fd:
            source_fd = self.filesystem._open_dir(root_fd, parts)
        try:
            self._copy_tree(source_fd, destination, [0, 0], ())
        finally:
            os.close(source_fd)

    def _copy_tree(
        self, source_fd: int, destination: Path, usage: list[int], prefix: tuple[str, ...]
    ) -> None:
        for name in os.listdir(source_fd):
            _relative_parts(name)
            relative = (*prefix, name)
            if len("/".join(relative)) > 1000:
                raise AgentError("operation.tooLarge", "Project path exceeds the limit")
            usage[0] += 1
            if usage[0] > self.max_entries:
                raise AgentError("operation.tooLarge", "Project has too many entries")
            metadata = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
            target = destination / name
            if stat.S_ISDIR(metadata.st_mode):
                target.mkdir(mode=0o700)
                child = self.filesystem._open_dir(source_fd, (name,))
                try:
                    self._copy_tree(child, target, usage, relative)
                finally:
                    os.close(child)
            elif stat.S_ISREG(metadata.st_mode):
                if metadata.st_size > self.max_file_bytes:
                    raise AgentError("operation.tooLarge", "Project file exceeds the limit")
                source, opened_metadata = self.filesystem._open_regular(source_fd, name)
                if opened_metadata.st_size > self.max_file_bytes:
                    os.close(source)
                    raise AgentError("operation.tooLarge", "Project file exceeds the limit")
                try:
                    file_size = 0
                    with os.fdopen(source, "rb") as handle, target.open("xb") as output:
                        while chunk := handle.read(1024 * 1024):
                            file_size += len(chunk)
                            usage[1] += len(chunk)
                            if file_size > self.max_file_bytes or usage[1] > self.max_bytes:
                                raise AgentError(
                                    "operation.tooLarge", "Project exceeds the size limit"
                                )
                            output.write(chunk)
                    target.chmod(0o600)
                except BaseException:
                    target.unlink(missing_ok=True)
                    raise
            else:
                raise AgentError(
                    "openscad.unsafeProject", "Project contains a link or special file"
                )

    def _extract_archive(self, archive_path: Path, destination: Path) -> None:
        usage = [0, 0]
        names: set[tuple[str, ...]] = set()
        try:
            with zipfile.ZipFile(archive_path) as archive:
                for info in archive.infolist():
                    raw_name = info.filename.rstrip("/")
                    try:
                        parts = _relative_parts(raw_name)
                    except AgentError as exc:
                        raise AgentError(
                            "openscad.unsafeProject", "Archive contains an unsafe path"
                        ) from exc
                    if not parts or len(raw_name) > 1000 or parts in names:
                        raise AgentError("openscad.invalidArchive", "Archive path is invalid")
                    names.add(parts)
                    usage[0] += 1
                    if usage[0] > self.max_entries:
                        raise AgentError("operation.tooLarge", "Archive has too many entries")
                    mode = info.external_attr >> 16
                    file_type = stat.S_IFMT(mode)
                    if (
                        info.flag_bits & 1
                        or stat.S_ISLNK(mode)
                        or file_type
                        not in {
                            0,
                            stat.S_IFREG,
                            stat.S_IFDIR,
                        }
                    ):
                        raise AgentError(
                            "openscad.unsafeProject", "Archive contains an unsafe entry"
                        )
                    if info.file_size > self.max_file_bytes:
                        raise AgentError("operation.tooLarge", "Archive file exceeds the limit")
                    usage[1] += info.file_size
                    if usage[1] > self.max_bytes:
                        raise AgentError("operation.tooLarge", "Archive exceeds the size limit")

                    target = destination.joinpath(*parts)
                    if info.is_dir():
                        target.mkdir(parents=True, exist_ok=True, mode=0o700)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    actual = 0
                    with archive.open(info) as source, target.open("xb") as output:
                        while chunk := source.read(1024 * 1024):
                            actual += len(chunk)
                            if actual > info.file_size or actual > self.max_file_bytes:
                                raise AgentError(
                                    "operation.tooLarge", "Archive file exceeds the limit"
                                )
                            output.write(chunk)
                    if actual != info.file_size:
                        raise AgentError("openscad.invalidArchive", "Archive entry size changed")
                    target.chmod(0o600)
        except AgentError:
            raise
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise AgentError("openscad.invalidArchive", "OpenSCAD archive is invalid") from exc

    def _validate_entrypoint(self, project: Path, entrypoint: str) -> Path:
        path = project.joinpath(*_relative_parts(entrypoint))
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise AgentError("path.notFound", "OpenSCAD entrypoint is unavailable") from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > self.max_file_bytes
            or path.suffix.lower() != ".scad"
        ):
            raise AgentError("path.notFile", "OpenSCAD entrypoint is invalid")
        return path

    @staticmethod
    def _output_name(entrypoint: str, output: str) -> str:
        stem = Path(entrypoint).stem
        name = f"{stem}.{'off' if output == 'preview' else 'stl'}"
        if len(name) > 255 or len(name.encode("utf-8")) > 255:
            raise AgentError("path.invalid", "OpenSCAD output filename is too long")
        return name

    @staticmethod
    def _preview_names(entrypoint: str, cameras: dict[str, str]) -> dict[str, str]:
        stem = Path(entrypoint).stem
        names = {view: f"{stem}-{view}.png" for view in cameras}
        if any(len(name) > 255 or len(name.encode("utf-8")) > 255 for name in names.values()):
            raise AgentError("path.invalid", "OpenSCAD preview filename is too long")
        return names

    @staticmethod
    def _validate_stl(path: Path) -> None:
        try:
            with path.open("rb") as handle:
                header = handle.read(84)
                if len(header) != 84:
                    raise ValueError
                triangles = struct.unpack_from("<I", header, 80)[0]
                if triangles == 0 or path.stat().st_size != 84 + triangles * 50:
                    raise ValueError
                for _ in range(triangles):
                    values = struct.unpack("<12fH", handle.read(50))
                    if not all(math.isfinite(value) for value in values[:12]):
                        raise ValueError
        except (OSError, struct.error, ValueError) as exc:
            raise AgentError(
                "openscad.invalidOutput", "OpenSCAD produced an invalid binary STL"
            ) from exc

    @staticmethod
    def _validate_off(path: Path) -> None:
        try:
            lines = [
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            header = lines[0].split(maxsplit=1)
            if header[0] != "OFF":
                raise ValueError
            if len(header) == 2:
                lines[0:1] = ["OFF", header[1]]
            if len(lines) < 3:
                raise ValueError
            counts = lines[1].split()
            if len(counts) != 3:
                raise ValueError
            vertices, faces, edges = (int(value) for value in counts)
            if vertices < 3 or faces < 1 or edges < 0 or len(lines) != 2 + vertices + faces:
                raise ValueError
            for line in lines[2 : 2 + vertices]:
                values = line.split()
                if len(values) != 3 or not all(math.isfinite(float(value)) for value in values):
                    raise ValueError
            triangles = 0
            for line in lines[2 + vertices :]:
                values = line.split()
                count = int(values[0])
                if count < 3 or len(values) not in {count + 1, count + 4, count + 5}:
                    raise ValueError
                indices = [int(value) for value in values[1 : count + 1]]
                if any(index < 0 or index >= vertices for index in indices):
                    raise ValueError
                if not all(math.isfinite(float(value)) for value in values[count + 1 :]):
                    raise ValueError
                triangles += count - 2
            if triangles < 1:
                raise ValueError
        except (OSError, UnicodeError, ValueError, IndexError) as exc:
            raise AgentError(
                "openscad.invalidOutput", "OpenSCAD produced an invalid OFF preview"
            ) from exc

    def _validate_output_file(self, path: Path) -> int:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise AgentError("openscad.invalidOutput", "OpenSCAD output is unavailable") from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size == 0
            or metadata.st_size > self.max_artifact_bytes
        ):
            raise AgentError("openscad.invalidOutput", "OpenSCAD output exceeds the limit")
        return metadata.st_size

    def _descriptor(
        self,
        path: Path,
        *,
        backend_id: str,
        session_id: str,
        render_id: str,
        name: str,
        content_type: str,
    ) -> ArtifactDescriptor:
        size = self._validate_output_file(path)
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        return ArtifactDescriptor(
            artifactId=f"openscad-{render_id}-{name}",
            backendId=backend_id,
            sessionId=session_id,
            path=f"openscad/{render_id}/{path.name}",
            filename=path.name,
            size=size,
            sha256=digest,
            contentType=content_type,
        )

    def _store(self, descriptor: ArtifactDescriptor, path: Path) -> None:
        with path.open("rb") as handle:
            self.cache.store(descriptor, handle)

    async def render(
        self, arguments: OpenScadRenderArguments, backend_id: str, *, six_views: bool = False
    ) -> dict:
        cameras = CAMERAS_6 if six_views else CAMERAS
        if not isinstance(arguments.source, OpenScadArchiveSource):
            self.filesystem.resolve_directory(arguments.source.path)
        await self._validated_executable(arguments.executable_path)

        with tempfile.TemporaryDirectory(prefix="render-", dir=self.temp_root) as temporary:
            work = Path(temporary)
            project = work / "project"
            output = work / "output"
            project.mkdir(mode=0o700)
            output.mkdir(mode=0o700)
            if isinstance(arguments.source, OpenScadArchiveSource):
                artifact = arguments.source.artifact
                if (
                    artifact.backend_id != backend_id
                    or artifact.session_id != arguments.session_id
                    or artifact.content_type != "application/zip"
                    or Path(artifact.filename).suffix.casefold() != ".zip"
                ):
                    raise AgentError("cache.invalidDescriptor", "Archive identity does not match")
                archive = await self.transfer.download(
                    arguments.source.transfer,
                    work / "download",
                    expected_size=artifact.size,
                    expected_sha256=artifact.sha256,
                )
                try:
                    await asyncio.to_thread(self._extract_archive, archive, project)
                finally:
                    archive.unlink(missing_ok=True)
            else:
                await asyncio.to_thread(self._copy_workspace_project, arguments, project)
            entrypoint = self._validate_entrypoint(project, arguments.entrypoint)
            filename = self._output_name(arguments.entrypoint, arguments.output)
            output_path = output / filename
            if arguments.output == "preview":
                source = entrypoint.read_bytes()
                if len(source) + len(b"$preview=true;\n") > self.max_file_bytes:
                    raise AgentError("file.tooLarge", "OpenSCAD entrypoint exceeds the limit")
                entrypoint.write_bytes(b"$preview=true;\n" + source)
            format_argument = "off" if arguments.output == "preview" else "binstl"
            command = [
                os.fspath(self.executable),
                f"--export-format={format_argument}",
                "-o",
                os.fspath(output_path),
                os.fspath(entrypoint),
            ]
            if arguments.output == "preview" and self.manifold_available:
                command.insert(1, "--backend=manifold")
            diagnostic = await self._run(
                command,
                work,
                diagnostic_limit=self.max_diagnostics,
                error_code="openscad.renderFailed",
                timeout_code="openscad.timedOut",
            )
            diagnostics = [diagnostic]
            try:
                self._validate_output_file(output_path)
                validator = (
                    self._validate_off if arguments.output == "preview" else self._validate_stl
                )
                await asyncio.to_thread(validator, output_path)
            except AgentError as exc:
                if exc.params is None:
                    exc.params = self._failure_params(diagnostic)
                raise

            preview_paths: list[tuple[str, Path]] = []
            if arguments.output == "preview" and arguments.preview_images:
                preview_names = self._preview_names(arguments.entrypoint, cameras)
                preview_slots = asyncio.Semaphore(2)

                async def render_preview(view: str, camera: str):
                    preview_path = output / preview_names[view]
                    async with preview_slots:
                        preview_diagnostic = await self._run(
                            [
                                os.fspath(self.xvfb),
                                "-a",
                                os.fspath(self.executable),
                                "-D",
                                "$preview=true",
                                "-o",
                                os.fspath(preview_path),
                                f"--camera={camera}",
                                "--autocenter",
                                "--viewall",
                                "--projection=o",
                                "--imgsize=800,600",
                                os.fspath(entrypoint),
                            ],
                            work,
                            diagnostic_limit=self.max_diagnostics,
                            error_code="openscad.previewFailed",
                            resource_divisor=2,
                        )
                    try:
                        self._validate_output_file(preview_path)
                        with preview_path.open("rb") as handle:
                            if handle.read(len(PNG_SIGNATURE)) != PNG_SIGNATURE:
                                raise AgentError(
                                    "openscad.invalidOutput",
                                    "OpenSCAD produced an invalid PNG preview",
                                )
                    except AgentError as exc:
                        if exc.params is None:
                            exc.params = self._failure_params(preview_diagnostic)
                        raise
                    return view, preview_path, preview_diagnostic

                preview_tasks = [
                    asyncio.create_task(render_preview(view, camera))
                    for view, camera in (
                        cameras.items()
                        if six_views
                        else sorted(cameras.items(), key=lambda item: item[0] != "top")
                    )
                ]
                try:
                    preview_results = await asyncio.gather(*preview_tasks)
                except BaseException:
                    for task in preview_tasks:
                        task.cancel()
                    await asyncio.gather(*preview_tasks, return_exceptions=True)
                    raise
                previews_by_view = {
                    view: (path, preview_diagnostic)
                    for view, path, preview_diagnostic in preview_results
                }
                preview_paths = [(view, previews_by_view[view][0]) for view in cameras]
                diagnostics.extend(previews_by_view[view][1] for view in cameras)

            if (
                output_path.stat().st_size + sum(path.stat().st_size for _, path in preview_paths)
                > self.cache.max_bytes
            ):
                raise AgentError("operation.tooLarge", "Rendered artifacts exceed cache capacity")
            render_id = uuid4().hex
            artifact = await asyncio.to_thread(
                self._descriptor,
                output_path,
                backend_id=backend_id,
                session_id=arguments.session_id,
                render_id=render_id,
                name=arguments.output,
                content_type="model/vnd.off" if arguments.output == "preview" else "model/stl",
            )
            previews = [
                {
                    "view": view,
                    "artifact": await asyncio.to_thread(
                        self._descriptor,
                        path,
                        backend_id=backend_id,
                        session_id=arguments.session_id,
                        render_id=render_id,
                        name=view,
                        content_type="image/png",
                    ),
                }
                for view, path in preview_paths
            ]
            await asyncio.to_thread(self._store, artifact, output_path)
            for preview, (_, path) in zip(previews, preview_paths, strict=True):
                await asyncio.to_thread(self._store, preview["artifact"], path)
            diagnostic_text = "\n".join(
                text for item in diagnostics for text in (item["stdout"], item["stderr"]) if text
            )
            return {
                "output": arguments.output,
                "artifact": artifact.model_dump(by_alias=True),
                "previews": [
                    {
                        "view": preview["view"],
                        "artifact": preview["artifact"].model_dump(by_alias=True),
                    }
                    for preview in previews
                ],
                "diagnostics": diagnostic_text[: self.max_diagnostics],
                "diagnosticsTruncated": len(diagnostic_text) > self.max_diagnostics
                or any(item["stdoutTruncated"] or item["stderrTruncated"] for item in diagnostics),
            }
