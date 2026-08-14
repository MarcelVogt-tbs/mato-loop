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
from .process import ProcessRunner
from .protocol import (
    ArtifactDescriptor,
    OpenScadArchiveSource,
    OpenScadRenderArguments,
)
from .transfer import TransferClient
from .workspace import AgentError, Workspace, _parts

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
KNOWN_OPENSCAD = (Path("/usr/local/bin/openscad"), Path("/usr/bin/openscad"))


class OpenScadRunner:
    def __init__(
        self,
        config: AgentConfig,
        workspace: Workspace,
        cache: SessionCache,
        transfer: TransferClient,
    ) -> None:
        self.workspace = workspace
        self.cache = cache
        self.transfer = transfer
        self.timeout = config.default_timeout_seconds
        self.max_entries = config.max_operation_entries
        self.max_file_bytes = config.max_file_bytes
        self.max_bytes = config.max_transfer_bytes
        self.max_artifact_bytes = min(MAX_ARTIFACT_BYTES, self.max_bytes)
        self.max_diagnostics = min(config.max_output_bytes, 64 * 1024)
        # Bubblewrap cannot bind sources from Docker named volumes on every host.
        self.temp_root = Path(tempfile.mkdtemp(prefix="mato-loop-openscad-"))
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
        self.xvfb = self._find_executable()
        self.bwrap = self._find_bwrap()
        self.xvfb_identity = self._identity(self.xvfb)
        self.bwrap_identity = self._identity(self.bwrap)
        self.sandbox_available = self._probe_sandbox()

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

    def _find_executable(self) -> Path | None:
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

    def _find_bwrap(self) -> Path | None:
        try:
            return self._candidate(Path("/usr/bin/bwrap"))
        except AgentError:
            return None

    def _same_identity(self, path: Path | None, identity: tuple[int, int] | None) -> bool:
        if path is None or identity is None:
            return False
        try:
            return self._candidate(path) == path and self._identity(path) == identity
        except (AgentError, OSError):
            return False

    def _headless_available(self) -> bool:
        return self.sandbox_available and self._runtime_available()

    def _runtime_available(self) -> bool:
        return (
            self._same_identity(self.executable, self.executable_identity)
            and self._same_identity(self.xvfb, self.xvfb_identity)
            and self._same_identity(self.bwrap, self.bwrap_identity)
        )

    def _probe_sandbox(self) -> bool:
        if not self._runtime_available():
            return False
        with tempfile.TemporaryDirectory(prefix="probe-", dir=self.temp_root) as temporary:
            work = Path(temporary)
            project = work / "project"
            output = work / "output"
            project.mkdir(mode=0o700)
            output.mkdir(mode=0o700)
            (project / "probe.scad").write_text("cube(1);\n", encoding="ascii")
            try:
                result = subprocess.run(
                    self._sandbox_argv(
                        project,
                        output,
                        [
                            "/xvfb-run",
                            "-a",
                            "/openscad",
                            "-o",
                            "/output/probe.png",
                            "--imgsize=32,32",
                            "/project/probe.scad",
                        ],
                    ),
                    cwd=work,
                    env=self._environment(work),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
                with (output / "probe.png").open("rb") as handle:
                    signature = handle.read(len(PNG_SIGNATURE))
                    return result.returncode == 0 and signature == PNG_SIGNATURE
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
    ) -> dict:
        def set_limits() -> None:
            for kind, maximum in (
                (resource.RLIMIT_CPU, MAX_CPU_SECONDS),
                (resource.RLIMIT_AS, MAX_ADDRESS_SPACE_BYTES),
                (resource.RLIMIT_NPROC, MAX_PROCESSES),
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

    def _sandbox_argv(
        self,
        project: Path,
        output: Path,
        command: list[str],
    ) -> list[str]:
        if not self._runtime_available():
            raise AgentError(
                "openscad.headlessPreviewUnavailable", "Headless rendering is unavailable"
            )
        argv = [
            os.fspath(self.bwrap),
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--cap-drop",
            "ALL",
            "--clearenv",
        ]
        for path in ("/usr", "/bin", "/lib", "/lib64", "/etc/alternatives", "/etc/fonts"):
            if Path(path).exists():
                argv.extend(("--ro-bind", path, path))
        argv.extend(
            (
                "--dev",
                "/dev",
                "--tmpfs",
                "/tmp",
                "--dir",
                "/home",
                "--dir",
                "/home/.cache",
                "--dir",
                "/home/.config",
                "--ro-bind",
                os.fspath(self.executable),
                "/openscad",
                "--ro-bind",
                os.fspath(self.xvfb),
                "/xvfb-run",
                "--ro-bind",
                os.fspath(project),
                "/project",
                "--bind",
                os.fspath(output),
                "/output",
                "--setenv",
                "HOME",
                "/home",
                "--setenv",
                "LANG",
                "C.UTF-8",
                "--setenv",
                "LC_ALL",
                "C.UTF-8",
                "--setenv",
                "PATH",
                "/usr/local/bin:/usr/bin:/bin",
                "--setenv",
                "TMPDIR",
                "/tmp",
                "--setenv",
                "XDG_CACHE_HOME",
                "/home/.cache",
                "--setenv",
                "XDG_CONFIG_HOME",
                "/home/.config",
                "--setenv",
                "XDG_RUNTIME_DIR",
                "/tmp",
                "--chdir",
                "/project",
                "--",
                *command,
            )
        )
        return argv

    def _copy_workspace_project(
        self, arguments: OpenScadRenderArguments, destination: Path
    ) -> None:
        assert arguments.current_directory is not None
        assert arguments.root_id is not None
        assert arguments.root_revision is not None
        resolved = self.workspace.resolve_directory(
            arguments.source.path,
            arguments.current_directory,
            arguments.root_id,
            arguments.root_revision,
        )
        parts = _parts(resolved["relativePath"])
        with self.workspace._root_fd(arguments.root_id, arguments.root_revision) as root_fd:
            source_fd = self.workspace._open_dir(root_fd, parts)
        try:
            self._copy_tree(source_fd, destination, [0, 0], ())
        finally:
            os.close(source_fd)

    def _copy_tree(
        self, source_fd: int, destination: Path, usage: list[int], prefix: tuple[str, ...]
    ) -> None:
        for name in os.listdir(source_fd):
            _parts(name)
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
                child = self.workspace._open_dir(source_fd, (name,))
                try:
                    self._copy_tree(child, target, usage, relative)
                finally:
                    os.close(child)
            elif stat.S_ISREG(metadata.st_mode):
                if metadata.st_size > self.max_file_bytes:
                    raise AgentError("operation.tooLarge", "Project file exceeds the limit")
                source, opened_metadata = self.workspace._open_regular(source_fd, name)
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
                        parts = _parts(raw_name)
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
        path = project.joinpath(*_parts(entrypoint))
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
    def _output_names(entrypoint: str) -> tuple[str, dict[str, str]]:
        stem = Path(entrypoint).stem
        model = f"{stem}.stl"
        previews = {view: f"{stem}-{view}.png" for view in CAMERAS}
        if any(
            len(name) > 255 or len(name.encode("utf-8")) > 255
            for name in (model, *previews.values())
        ):
            raise AgentError("path.invalid", "OpenSCAD output filename is too long")
        return model, previews

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

    async def render(self, arguments: OpenScadRenderArguments, backend_id: str) -> dict:
        if not isinstance(arguments.source, OpenScadArchiveSource):
            assert arguments.current_directory is not None
            assert arguments.root_id is not None
            assert arguments.root_revision is not None
            self.workspace.resolve_directory(
                arguments.current_directory,
                root_id=arguments.root_id,
                revision=arguments.root_revision,
            )
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
            self._validate_entrypoint(project, arguments.entrypoint)
            model_filename, preview_filenames = self._output_names(arguments.entrypoint)

            diagnostics = []
            model_path = output / model_filename
            diagnostic = await self._run(
                self._sandbox_argv(
                    project,
                    output,
                    [
                        "/openscad",
                        "--export-format=binstl",
                        "-o",
                        f"/output/{model_filename}",
                        f"/project/{arguments.entrypoint}",
                    ],
                ),
                work,
                diagnostic_limit=self.max_diagnostics,
                error_code="openscad.renderFailed",
                timeout_code="openscad.timedOut",
            )
            diagnostics.append(diagnostic)
            try:
                self._validate_output_file(model_path)
                await asyncio.to_thread(self._validate_stl, model_path)
            except AgentError as exc:
                if exc.params is None:
                    exc.params = self._failure_params(diagnostic)
                raise

            preview_paths = []
            for name, camera in CAMERAS.items():
                filename = preview_filenames[name]
                path = output / filename
                diagnostic = await self._run(
                    self._sandbox_argv(
                        project,
                        output,
                        [
                            "/xvfb-run",
                            "-a",
                            "/openscad",
                            "-o",
                            f"/output/{filename}",
                            f"--camera={camera}",
                            "--autocenter",
                            "--viewall",
                            "--projection=o",
                            "--imgsize=800,600",
                            f"/project/{arguments.entrypoint}",
                        ],
                    ),
                    work,
                    diagnostic_limit=self.max_diagnostics,
                    error_code="openscad.previewFailed",
                )
                diagnostics.append(diagnostic)
                try:
                    self._validate_output_file(path)
                    with path.open("rb") as handle:
                        signature = handle.read(len(PNG_SIGNATURE))
                    if signature != PNG_SIGNATURE:
                        raise AgentError(
                            "openscad.invalidOutput", "OpenSCAD produced an invalid PNG"
                        )
                except AgentError as exc:
                    if exc.params is None:
                        exc.params = self._failure_params(diagnostic)
                    raise
                preview_paths.append((name, path))

            output_size = model_path.stat().st_size + sum(
                path.stat().st_size for _, path in preview_paths
            )
            if output_size > self.cache.max_bytes:
                raise AgentError("operation.tooLarge", "Rendered artifacts exceed cache capacity")
            render_id = uuid4().hex
            model = await asyncio.to_thread(
                self._descriptor,
                model_path,
                backend_id=backend_id,
                session_id=arguments.session_id,
                render_id=render_id,
                name="model",
                content_type="model/stl",
            )
            previews = []
            for name, path in preview_paths:
                previews.append(
                    await asyncio.to_thread(
                        self._descriptor,
                        path,
                        backend_id=backend_id,
                        session_id=arguments.session_id,
                        render_id=render_id,
                        name=name,
                        content_type="image/png",
                    )
                )
            artifacts = [
                (model, model_path),
                *(
                    (preview, path)
                    for preview, (_, path) in zip(previews, preview_paths, strict=True)
                ),
            ]
            for descriptor, path in artifacts:
                await asyncio.to_thread(self._store, descriptor, path)
            diagnostic_text = "\n".join(
                text
                for diagnostic in diagnostics
                for text in (diagnostic["stdout"], diagnostic["stderr"])
                if text
            )
            return {
                "model": model.model_dump(by_alias=True),
                "previews": [
                    {
                        "view": view,
                        "artifact": preview.model_dump(by_alias=True),
                    }
                    for preview, (view, path) in zip(previews, preview_paths, strict=True)
                ],
                "diagnostics": diagnostic_text[: self.max_diagnostics],
                "diagnosticsTruncated": len(diagnostic_text) > self.max_diagnostics
                or any(
                    diagnostic["stdoutTruncated"] or diagnostic["stderrTruncated"]
                    for diagnostic in diagnostics
                ),
            }
