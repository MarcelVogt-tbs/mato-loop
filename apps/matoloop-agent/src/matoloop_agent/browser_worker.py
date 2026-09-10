"""Credential-free Linux browser process. Only the parent owns the control pipes."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import ctypes
import hashlib
import hmac
import json
import mimetypes
import os
import re
import resource
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
from collections import deque
from importlib.metadata import version
from io import BytesIO
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from PIL import Image
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from .browser_contracts import (
    MAX_CLIPBOARD_BYTES,
    MAX_FILE_BYTES,
    BrowserActionResult,
    BrowserClipboardArguments,
    BrowserClipboardResult,
    BrowserControlArguments,
    BrowserExecuteArguments,
    BrowserFileArguments,
    BrowserNavigationArguments,
    BrowserNavigationResult,
    BrowserResizeArguments,
    BrowserResizeResult,
    BrowserStatus,
    decode_chunk,
)
from .filesystem import AgentError


def fail(code: str) -> AgentError:
    return AgentError(f"browser.{code}", "Browser operation failed")


def launch_error_code(error: Exception) -> str:
    message = str(error).lower()
    if (
        "profile appears to be in use by another" in message
        or "opening in existing browser session." in message
        or ("processsingleton" in message and "/singletonlock: file exists" in message)
    ):
        return "profileInUse"
    if any(
        marker in message
        for marker in ("sandbox", "operation not permitted", "failed to move to new namespace")
    ):
        return "sandboxUnavailable"
    return "notReady"


class BrowserWorker:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.context = None
        self.playwright = None
        self.processes: list[subprocess.Popen] = []
        self.tasks: set[asyncio.Task] = set()
        self.tabs: dict[str, object] = {}
        self.files: dict[str, dict] = {}
        self.pending_choosers: dict[object, object] = {}
        self.diagnostics = deque(maxlen=50)
        self.server = None
        self.temporary = None
        self.manual = True
        self.vnc_lock = asyncio.Lock()
        self.vnc_generation = 0
        self.vnc_sessions: dict[asyncio.Task, tuple[asyncio.subprocess.Process, dict]] = {}
        self.clipboard_owner = None
        self.document_ids: dict[object, str] = {}
        self.snapshot_key = os.urandom(32)
        self.geometry = {"width": 3840, "height": 2160}
        self.resize_mode = None
        self.page_sessions: dict[object, asyncio.Task] = {}
        self.loading_frames: dict[object, set[str]] = {}
        self.page_focus_state: dict[object, dict] = {}
        self.page_history: dict[object, dict] = {}
        self.active_tab_id = None
        self.active_window_id = None
        self.native_chrome = False
        self.windows: set[int] = set()
        self.startup_stage = "storage"

    def task(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def start(self, *, probe: bool = False, allow_network: bool = True) -> dict:
        self.startup_stage = "storage"
        if not allow_network:
            raise fail("invalidTarget")
        try:
            with tempfile.TemporaryFile(dir=self.root):
                pass
        except OSError:
            raise fail("notReady") from None
        missing = [
            name
            for name in ("Xvfb", "xrandr", "xprop", "openbox", "x11vnc", "xclip")
            if not shutil.which(name)
        ]
        if missing:
            return BrowserStatus(
                state="missing",
                missing=missing,
                errorCode="browser.dependenciesMissing",
                playwrightVersion=version("playwright"),
            ).model_dump(by_alias=True)
        # Keep the Unix socket path below Linux's 108-byte sockaddr_un limit.
        self.temporary = tempfile.TemporaryDirectory(prefix="matoloop-browser-")
        live = Path(self.temporary.name)
        self.live = live
        auth = live / "Xauthority"
        # FamilyWild cookie authorizes only clients with this private file, never TCP.
        cookie = os.urandom(16)
        auth.write_bytes(
            struct.pack(">H", 65535)
            + b"".join(
                struct.pack(">H", len(v)) + v for v in (b"", b"", b"MIT-MAGIC-COOKIE-1", cookie)
            )
        )
        auth.chmod(0o600)
        self.startup_stage = "display"
        read_fd, write_fd = os.pipe()
        try:
            xvfb = subprocess.Popen(
                [
                    "Xvfb",
                    "-displayfd",
                    str(write_fd),
                    "-auth",
                    str(auth),
                    "-nolisten",
                    "tcp",
                    "-screen",
                    "0",
                    "3840x2160x24",
                    "-noreset",
                ],
                pass_fds=(write_fd,),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.processes.append(xvfb)
        finally:
            os.close(write_fd)
        os.set_blocking(read_fd, False)
        display = b""
        try:
            async with asyncio.timeout(10):
                while b"\n" not in display:
                    try:
                        chunk = os.read(read_fd, 32)
                        if not chunk:
                            raise fail("notReady")
                        display += chunk
                    except BlockingIOError:
                        await asyncio.sleep(0.05)
        finally:
            os.close(read_fd)
        self.environment = {
            **os.environ,
            "DISPLAY": ":" + str(int(display)),
            "XAUTHORITY": str(auth),
            "HOME": str(live),
            "XDG_CONFIG_HOME": str(live / "config"),
            "XDG_CACHE_HOME": str(live / "cache"),
            "XDG_RUNTIME_DIR": str(live),
        }
        await self.resize(1280, 800)
        self.processes.append(
            subprocess.Popen(
                ["openbox", "--sm-disable"],
                env=self.environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        )
        self.startup_stage = "vnc"
        # Verify X authentication, the WM and full-duplex inetd RFB before Chromium launch.
        self.server = await asyncio.start_unix_server(self.vnc_client, path=str(live / "vnc"))
        reader, writer = await asyncio.open_unix_connection(live / "vnc")
        try:
            banner = await asyncio.wait_for(reader.readexactly(12), 10)
            if not banner.startswith(b"RFB ") or any(p.poll() is not None for p in self.processes):
                raise fail("notReady")
        finally:
            writer.close()
            await writer.wait_closed()
        self.startup_stage = "chromium"
        self.playwright = await async_playwright().start()
        executable = self.playwright.chromium.executable_path
        if not Path(executable).is_file():
            return BrowserStatus(
                state="missing",
                missing=["chromium"],
                errorCode="browser.dependenciesMissing",
                playwrightVersion=version("playwright"),
            ).model_dump(by_alias=True)
        try:
            self.context = await self.playwright.chromium.launch_persistent_context(
                str(live / "probe" if probe else self.root / "profile"),
                headless=False,
                chromium_sandbox=True,
                env=self.environment,
                no_viewport=True,
                accept_downloads=True,
                downloads_path=str(live / "downloads"),
                args=[
                    "--disable-gpu",
                    "--start-maximized",
                    # Chromium app mode suppresses fullscreen exit overlays but retains
                    # native DevTools via Ctrl+Shift+I; --kiosk disables DevTools entirely.
                    "--force-app-mode",
                ],
                timeout=20_000,
            )
            await self.disable_translation_prompt()
        except Exception as exc:
            # Classify locally; raw Playwright errors can contain profile data and secrets.
            raise fail(launch_error_code(exc)) from None
        self.startup_stage = "page"
        self.context.set_default_timeout(8000)
        self.context.on(
            "requestfailed", lambda request: self.network_diagnostic("requestfailed", request.url)
        )
        self.context.on(
            "response",
            lambda response: self.network_diagnostic("response", response.url, response.status),
        )
        self.context.on("page", self.add_page)
        for page in self.context.pages:
            self.add_page(page)
        if not self.context.pages:
            raise fail("notReady")
        await self.context.pages[0].bring_to_front()
        await self.page_sessions[self.context.pages[0]]
        self.startup_stage = "resize"
        # Exercise the native minimum-size workaround and both resize directions at startup.
        await self.resize(320, 240)
        await self.resize(1280, 800)
        await self.context.pages[0].screenshot(type="png", timeout=8000)
        return BrowserStatus(
            state="ready",
            browserVersion=self.context.browser.version,
            playwrightVersion=version("playwright"),
        ).model_dump(by_alias=True)

    async def x_command(self, *arguments: str) -> str:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            env=self.environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with asyncio.timeout(5):
                output, _ = await process.communicate()
            if process.returncode:
                raise fail("actionFailed")
            return output.decode("ascii")
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()

    async def resize(self, width: int, height: int) -> dict:
        requested = BrowserResizeResult(width=width, height=height).model_dump()
        query = await self.x_command("xrandr", "--query")
        maximum = re.search(r"maximum (\d+) x (\d+)", query)
        if not maximum or width > int(maximum[1]) or height > int(maximum[2]):
            raise fail("invalidTarget")
        if self.context:
            # Chromium's 500px minimum hint otherwise clips the maximized window on mobile.
            # Only windows on this authenticated private desktop can appear in this property.
            windows = await self.x_command("xprop", "-root", "_NET_CLIENT_LIST")
            for window in re.findall(r"0x[0-9a-fA-F]+", windows):
                await self.x_command("xprop", "-id", window, "-remove", "WM_NORMAL_HINTS")
        mode = f"matoloop-{width}x{height}"
        if not re.search(rf"^\s+{re.escape(mode)}\s", query, re.MULTILINE):
            # Virtual output timings only; no physical monitor or caller-supplied modeline.
            await self.x_command(
                "xrandr",
                "--newmode",
                mode,
                f"{(width + 24) * (height + 3) * 60 / 1_000_000:.6f}",
                str(width),
                str(width + 8),
                str(width + 16),
                str(width + 24),
                str(height),
                str(height + 1),
                str(height + 2),
                str(height + 3),
            )
        await self.x_command("xrandr", "--addmode", "screen", mode)
        await self.x_command("xrandr", "--output", "screen", "--mode", mode)
        applied = re.search(r"current (\d+) x (\d+)", await self.x_command("xrandr", "--query"))
        if not applied:
            raise fail("actionFailed")
        self.geometry = BrowserResizeResult(
            width=int(applied[1]), height=int(applied[2])
        ).model_dump()
        if self.geometry != requested:
            raise fail("actionFailed")
        previous, self.resize_mode = self.resize_mode, mode
        if previous and previous != mode:
            await self.x_command("xrandr", "--delmode", "screen", previous)
            await self.x_command("xrandr", "--rmmode", previous)
        return self.geometry

    def network_diagnostic(self, event: str, url: str, status: int | None = None) -> None:
        try:
            if len(url) > 8192:
                raise ValueError("URL exceeds diagnostic limit")
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"}:
                raise ValueError("Non-web URL")
            # Keep resource paths, not authentication userinfo, query tokens or fragments.
            target = urlunsplit(
                (parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, "", "")
            )
        except ValueError:
            target = "[URL omitted]"
        prefix = f"{event} {status}" if status is not None else event
        self.diagnostics.append(f"{prefix} {target}"[:500])

    async def vnc_client(self, reader, writer) -> None:
        generation = self.vnc_generation
        process = None
        vnc_writer = None
        parent, child = socket.socketpair()
        pumps = []
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            async with self.vnc_lock:
                if generation != self.vnc_generation:
                    return
                environment = {**self.environment, "X11VNC_REMOTE": "MATOLOOP_" + uuid4().hex}
                process = await asyncio.create_subprocess_exec(
                    "x11vnc",
                    "-inetd",
                    "-display",
                    environment["DISPLAY"],
                    "-auth",
                    environment["XAUTHORITY"],
                    "-nopw",
                    "-quiet",
                    "-noxdamage",
                    "-xrandr",
                    "resize",
                    "-norc",
                    "-nocmds",
                    "-nosel",
                    "-input",
                    "KMB",
                    *([] if self.manual else ["-viewonly"]),
                    stdin=child,
                    stdout=child,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=environment,
                )
                self.vnc_sessions[task] = (process, environment)
            child.close()
            vnc_reader, vnc_writer = await asyncio.open_connection(sock=parent)

            async def pump(source, target):
                while chunk := await source.read(65_536):
                    target.write(chunk)
                    await target.drain()

            pumps = [
                asyncio.create_task(pump(reader, vnc_writer)),
                asyncio.create_task(pump(vnc_reader, writer)),
            ]
            await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
        except (OSError, ConnectionError):
            pass
        finally:
            self.vnc_sessions.pop(task, None)
            child.close()
            for pump_task in pumps:
                pump_task.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            if process is not None:
                with contextlib.suppress(ProcessLookupError):
                    # Let x11vnc detach and remove its System V shared-memory segments.
                    process.terminate()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(process.wait()), 2)
                if process.returncode is None:
                    process.kill()
                await process.wait()
            if vnc_writer is not None:
                vnc_writer.close()
                with contextlib.suppress(ConnectionError):
                    await vnc_writer.wait_closed()
            else:
                parent.close()
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
            self.tasks.discard(task)

    async def control(self, manual: bool) -> dict:
        async with self.vnc_lock:
            if manual and self.manual:
                return {"ok": True}
            self.vnc_generation += 1
            self.manual = False
            self.pending_choosers.clear()
            sessions = list(self.vnc_sessions.items())
            if manual:
                # Rotation drops partial RFB messages and every queued byte before unlocking.
                # Keeping the same stream would require a second, complete RFB input parser.
                for task, _ in sessions:
                    task.cancel()
                await asyncio.gather(*(task for task, _ in sessions), return_exceptions=True)
                self.manual = True
                return {"ok": True}
            release = "clear_keys;" + ";".join(
                f"fakebuttonevent:{button},0" for button in range(1, 6)
            )
            try:
                for _, (server, environment) in sessions:
                    # x11vnc can lose PropertyNotify during startup/resize. Reissue the
                    # idempotent fence until acknowledged, never extend the overall deadline.
                    async with asyncio.timeout(5):
                        while server.returncode is None:
                            process = await asyncio.create_subprocess_exec(
                                "x11vnc",
                                "-norc",
                                "-nocmds",
                                "-display",
                                environment["DISPLAY"],
                                "-auth",
                                environment["XAUTHORITY"],
                                "-R",
                                "script:viewonly;" + release,
                                "-Q",
                                "viewonly",
                                env={**environment, "X11VNC_SYNC_TIMEOUT": "0.25"},
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.DEVNULL,
                            )
                            try:
                                try:
                                    output = await process.stdout.readexactly(1025)
                                except asyncio.IncompleteReadError as exc:
                                    output = exc.partial
                                if len(output) > 1024:
                                    raise fail("notReady")
                                await process.wait()
                                if (
                                    process.returncode == 0
                                    and b"ans=viewonly:1" in output.splitlines()
                                ):
                                    break
                            finally:
                                if process.returncode is None:
                                    process.kill()
                                await process.wait()
                            await asyncio.sleep(0.05)
                # X11 retains held input after a viewer exits; no live VNC server is required.
                await self.x_command(
                    "x11vnc",
                    "-norc",
                    "-nocmds",
                    "-display",
                    self.environment["DISPLAY"],
                    "-auth",
                    self.environment["XAUTHORITY"],
                    "-R",
                    "DIRECT:script:" + release,
                )
            except BaseException:
                for task, _ in sessions:
                    task.cancel()
                await asyncio.gather(*(task for task, _ in sessions), return_exceptions=True)
                raise
        return {"ok": True}

    async def copy_clipboard(self, args: BrowserClipboardArguments) -> dict:
        if not self.manual:
            raise fail("busy")
        environment = next(
            (env for server, env in self.vnc_sessions.values() if server.returncode is None), None
        )
        if environment is None:
            raise fail("actionFailed")

        # Xlib's native event layout (XIDs and Time are unsigned long, even on LP64).
        class SelectionNotify(ctypes.Structure):
            _fields_ = [
                ("type", ctypes.c_int),
                ("serial", ctypes.c_ulong),
                ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p),
                ("window", ctypes.c_ulong),
                ("subtype", ctypes.c_int),
                ("owner", ctypes.c_ulong),
                ("selection", ctypes.c_ulong),
                ("timestamp", ctypes.c_ulong),
                ("selection_timestamp", ctypes.c_ulong),
            ]

        display = None
        try:
            x11 = ctypes.CDLL("libX11.so.6")
            fixes, xtest = ctypes.CDLL("libXfixes.so.3"), ctypes.CDLL("libXtst.so.6")
            pointer, ulong, integer = ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int
            for library, name, arguments, result in (
                (x11, "XOpenDisplay", [ctypes.c_char_p], pointer),
                (x11, "XCloseDisplay", [pointer], integer),
                (x11, "XDefaultRootWindow", [pointer], ulong),
                (x11, "XInternAtom", [pointer, ctypes.c_char_p, integer], ulong),
                (x11, "XSync", [pointer, integer], integer),
                (x11, "XPending", [pointer], integer),
                (x11, "XNextEvent", [pointer, pointer], integer),
                (x11, "XGetSelectionOwner", [pointer, ulong], ulong),
                (x11, "XKeysymToKeycode", [pointer, ulong], ctypes.c_ubyte),
                (fixes, "XFixesQueryExtension", [pointer, pointer, pointer], integer),
                (fixes, "XFixesSelectSelectionInput", [pointer, ulong, ulong, ulong], None),
                (
                    xtest,
                    "XTestQueryExtension",
                    [pointer, pointer, pointer, pointer, pointer],
                    integer,
                ),
                (xtest, "XTestFakeKeyEvent", [pointer, ctypes.c_uint, integer, ulong], integer),
            ):
                function = getattr(library, name)
                function.argtypes, function.restype = arguments, result
            # No await while changing this credential-free worker's process-local environment.
            previous_auth = os.environ.get("XAUTHORITY")
            os.environ["XAUTHORITY"] = environment["XAUTHORITY"]
            try:
                display = x11.XOpenDisplay(environment["DISPLAY"].encode("ascii"))
            finally:
                if previous_auth is None:
                    os.environ.pop("XAUTHORITY", None)
                else:
                    os.environ["XAUTHORITY"] = previous_auth
            event_base, error_base = integer(), integer()
            if not display or not fixes.XFixesQueryExtension(
                display, ctypes.byref(event_base), ctypes.byref(error_base)
            ):
                raise fail("actionFailed")
            major, minor, test_event, test_error = integer(), integer(), integer(), integer()
            if not xtest.XTestQueryExtension(
                display,
                ctypes.byref(test_event),
                ctypes.byref(test_error),
                ctypes.byref(major),
                ctypes.byref(minor),
            ):
                raise fail("actionFailed")
            control_key, copy_key = (
                x11.XKeysymToKeycode(display, keysym) for keysym in (0xFFE3, 0x63)
            )
            if not control_key or not copy_key:
                raise fail("actionFailed")
            selection = x11.XInternAtom(display, b"CLIPBOARD", False)
            fixes.XFixesSelectSelectionInput(
                display, x11.XDefaultRootWindow(display), selection, 1
            )  # XFixesSetSelectionOwnerNotifyMask only, not PRIMARY or destruction events.
            x11.XSync(display, True)  # Subscribe and discard any pre-injection notifications.
            event = (ctypes.c_long * 24)()  # XEvent's public ABI includes 24 longs of padding.
            try:
                async with asyncio.timeout(2):
                    for key, down in (
                        (control_key, 1),
                        (copy_key, 1),
                        (copy_key, 0),
                        (control_key, 0),
                    ):
                        if not xtest.XTestFakeKeyEvent(display, key, down, 0):
                            raise fail("actionFailed")
                    x11.XSync(display, False)
                    while True:
                        if x11.XPending(display):
                            x11.XNextEvent(display, ctypes.byref(event))
                            notice = SelectionNotify.from_buffer(event)
                            if (
                                notice.type == event_base.value
                                and not notice.send_event
                                and notice.subtype == 0
                                and notice.selection == selection
                                and notice.owner
                                and notice.selection_timestamp
                            ):
                                owner = notice.owner
                                timestamp = notice.selection_timestamp
                                break
                        await asyncio.sleep(0.01)
                    result = await self.clipboard(
                        BrowserClipboardArguments(browserId=args.browser_id, action="read"),
                        require_text=True,
                    )
                    # Chromium's omnibox may announce the same owner/timestamp more than once.
                    # Refuse a different selection during the UTF-8 transfer, not duplicate events.
                    x11.XSync(display, False)
                    for _ in range(64):
                        if not x11.XPending(display):
                            break
                        x11.XNextEvent(display, ctypes.byref(event))
                        notice = SelectionNotify.from_buffer(event)
                        if (
                            notice.type != event_base.value
                            or notice.send_event
                            or notice.owner != owner
                            or notice.selection_timestamp != timestamp
                        ):
                            raise fail("actionFailed")
                    if x11.XPending(display) or x11.XGetSelectionOwner(display, selection) != owner:
                        raise fail("actionFailed")
                    return result
            finally:
                # Release both keys even after cancellation or a partially failed chord.
                copy_released = xtest.XTestFakeKeyEvent(display, copy_key, 0, 0)
                control_released = xtest.XTestFakeKeyEvent(display, control_key, 0, 0)
                x11.XSync(display, False)
                if not copy_released or not control_released:
                    with contextlib.suppress(Exception):
                        await self.control(False)
                    raise fail("actionFailed")
        except (OSError, AttributeError, TimeoutError):
            raise fail("actionFailed") from None
        finally:
            if display:
                x11.XCloseDisplay(display)

    async def clipboard(
        self, args: BrowserClipboardArguments, *, require_text: bool = False
    ) -> dict:
        if args.action == "copy":
            return await self.copy_clipboard(args)
        if args.action == "write":
            if not self.manual:
                raise fail("busy")
            if self.clipboard_owner is not None:
                with contextlib.suppress(ProcessLookupError):
                    self.clipboard_owner.kill()
                await self.clipboard_owner.wait()
            self.clipboard_owner = await asyncio.create_subprocess_exec(
                "xclip",
                "-selection",
                "clipboard",
                "-target",
                "UTF8_STRING",
                "-in",
                "-quiet",
                env=self.environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            self.clipboard_owner.stdin.write(args.text.encode("utf-8"))
            await self.clipboard_owner.stdin.drain()
            self.clipboard_owner.stdin.close()
        try:
            async with asyncio.timeout(4):
                while True:
                    process = await asyncio.create_subprocess_exec(
                        "xclip",
                        "-selection",
                        "clipboard",
                        "-target",
                        "UTF8_STRING",
                        "-out",
                        env=self.environment,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                        limit=MAX_CLIPBOARD_BYTES + 1,
                    )
                    try:
                        try:
                            data = await process.stdout.readexactly(MAX_CLIPBOARD_BYTES + 1)
                        except asyncio.IncompleteReadError as exc:
                            data = exc.partial
                        if len(data) > MAX_CLIPBOARD_BYTES:
                            if args.action == "write":
                                # The old selection may still own CLIPBOARD while xclip starts.
                                await asyncio.sleep(0.05)
                                continue
                            raise fail("fileTooLarge")
                        await process.wait()
                        if process.returncode not in {0, 1} or (
                            require_text and process.returncode
                        ):
                            raise fail("actionFailed")
                        text = data.decode("utf-8") if process.returncode == 0 else ""
                    finally:
                        if process.returncode is None:
                            process.kill()
                        await process.wait()
                    if args.action == "read" or text == args.text:
                        return BrowserClipboardResult(text=text).model_dump()
                    if self.clipboard_owner.returncode is not None:
                        raise fail("actionFailed")
                    await asyncio.sleep(0.05)
        except TimeoutError:
            raise fail("actionFailed") from None

    def add_page(self, page) -> None:
        if page in self.document_ids:
            return
        if len(self.tabs) >= 32:
            self.task(page.close())
            return
        tab_id = str(uuid4())
        self.tabs[tab_id] = page
        self.document_ids[page] = str(uuid4())
        self.loading_frames[page] = set()
        observation = asyncio.create_task(self.observe_page(page))
        self.page_sessions[page] = observation
        self.tasks.add(observation)

        def observed(task):
            self.tasks.discard(task)
            if not task.cancelled():
                task.exception()  # A native page may close before anyone awaits its CDP setup.

        observation.add_done_callback(observed)

        def closed():
            self.tabs.pop(tab_id, None)
            self.pending_choosers.pop(page, None)
            self.document_ids.pop(page, None)
            self.loading_frames.pop(page, None)
            self.page_focus_state.pop(page, None)
            self.page_history.pop(page, None)
            self.page_sessions.pop(page, None)

        def navigated(frame):
            self.pending_choosers.pop(page, None)
            if frame == page.main_frame:
                self.document_ids[page] = str(uuid4())

        page.on("close", closed)
        # Listening intercepts the native picker; the human supplies an opaque staged file.
        page.on("filechooser", lambda chooser: self.pending_choosers.__setitem__(page, chooser))
        page.on("framenavigated", navigated)
        page.on("download", lambda download: self.task(self.download(download)))
        page.on(
            "console",
            lambda message: self.diagnostics.append(f"{message.type}: {message.text[:500]}"),
        )
        page.on("pageerror", lambda error: self.diagnostics.append(str(error)[:500]))

    async def observe_page(self, page):
        session = await self.context.new_cdp_session(page)
        # Playwright otherwise emulates focus in background tabs. Never trust page-world APIs.
        await session.send("Emulation.setFocusEmulationEnabled", {"enabled": False})
        frames = self.loading_frames[page]
        session.on("Page.frameStartedLoading", lambda event: frames.add(event["frameId"]))
        session.on("Page.frameStoppedLoading", lambda event: frames.discard(event["frameId"]))
        await session.send("Page.enable")
        window = await session.send("Browser.getWindowForTarget")
        if window["windowId"] not in self.windows:
            self.windows.add(window["windowId"])
            # Once per browser window, never on status reads or native permission dialogs.
            await self.set_chrome(session, window, self.native_chrome)
        return session

    async def page_focus(self, session) -> dict:
        # A renderer swap can reapply Playwright's synthetic focus. This does not activate a tab.
        await session.send("Emulation.setFocusEmulationEnabled", {"enabled": False})
        tree = await session.send("Page.getFrameTree")
        world = await session.send(
            "Page.createIsolatedWorld",
            {"frameId": tree["frameTree"]["frame"]["id"], "worldName": "matoloop-native-state"},
        )
        result = await session.send(
            "Runtime.evaluate",
            {
                "expression": (
                    "({focused:document.hasFocus(),visible:document.visibilityState==='visible',"
                    "fullscreen:innerWidth===screen.width && innerHeight===screen.height})"
                ),
                "contextId": world["executionContextId"],
                "returnByValue": True,
            },
        )
        return result.get("result", {}).get("value", {})

    async def set_chrome(self, session, window, native_chrome: bool) -> None:
        # Chromium requires a normal intermediate state when leaving native fullscreen.
        if window["bounds"]["windowState"] == "fullscreen" and native_chrome:
            await session.send(
                "Browser.setWindowBounds",
                {"windowId": window["windowId"], "bounds": {"windowState": "normal"}},
            )
        await session.send(
            "Browser.setWindowBounds",
            {
                "windowId": window["windowId"],
                "bounds": {"windowState": "maximized" if native_chrome else "fullscreen"},
            },
        )
        # Chrome restores its minimum-width hint when switching native window modes.
        await self.resize(**self.geometry)

    async def navigation_state(self) -> dict:
        tabs, observed = [], {}
        for tab_id, page in list(self.tabs.items()):
            if page.is_closed():
                continue
            try:
                observation = self.page_sessions.get(page)
                if observation is None:
                    continue
                try:
                    async with asyncio.timeout(0.1):
                        session = await asyncio.shield(observation)
                except TimeoutError:
                    continue
                info = (await session.send("Target.getTargetInfo"))["targetInfo"]
                window = await session.send("Browser.getWindowForTarget")
                # Renderer commands can stall while a navigation waits for response headers.
                # Retain observed selection, not invented focus, so Stop remains reachable.
                try:
                    async with asyncio.timeout(0.1):
                        self.page_focus_state[page] = await self.page_focus(session)
                except (PlaywrightError, TimeoutError):
                    pass
                focus = self.page_focus_state.get(page, {})
                observed[tab_id] = (session, window, focus)
                tabs.append(
                    {
                        "id": tab_id,
                        "url": info["url"][:4096],
                        "title": info["title"][:500],
                        "loading": bool(self.loading_frames.get(page)),
                    }
                )
            except PlaywrightError:
                # A page may close or replace its execution context during this read.
                continue
        focused = next(
            (key for key, (_, _, state) in observed.items() if state.get("focused")), None
        )
        visible = [key for key, (_, _, state) in observed.items() if state.get("visible")]
        if focused:
            self.active_tab_id = focused
        elif self.active_tab_id not in visible:
            # Native omnibox/menu focus does not focus the document; retain its selected page.
            self.active_tab_id = next(
                (key for key in visible if observed[key][1]["windowId"] == self.active_window_id),
                visible[-1] if visible else None,
            )
        back = forward = loading = False
        if self.active_tab_id in observed:
            session, window, focus = observed[self.active_tab_id]
            self.active_window_id = window["windowId"]
            self.native_chrome = window["bounds"]["windowState"] != "fullscreen"
            page = self.tabs.get(self.active_tab_id)
            try:
                async with asyncio.timeout(0.1):
                    self.page_history[page] = await session.send("Page.getNavigationHistory")
            except (PlaywrightError, TimeoutError):
                pass
            history = self.page_history.get(page, {"currentIndex": -1, "entries": []})
            back = history["currentIndex"] > 0
            forward = history["currentIndex"] < len(history["entries"]) - 1
            loading = bool(self.loading_frames.get(self.tabs.get(self.active_tab_id)))
        return BrowserNavigationResult(
            tabs=tabs,
            activeTabId=self.active_tab_id,
            canGoBack=back,
            canGoForward=forward,
            loading=loading,
            nativeChrome=self.native_chrome,
        ).model_dump(by_alias=True)

    async def navigation(self, args: BrowserNavigationArguments) -> dict:
        if not self.context:
            raise fail("notConnected")
        if args.action == "state":
            return await self.navigation_state()
        if not self.manual:
            raise fail("busy")
        page = self.tabs.get(args.tab_id)
        if args.tab_id and (page is None or page.is_closed()):
            raise fail("invalidTarget")
        if args.action == "new":
            if len(self.tabs) >= 32:
                raise fail("busy")
            page = await self.context.new_page()
            await self.page_sessions[page]
            await page.bring_to_front()
            if args.url:
                await self.navigation_command(
                    await self.page_sessions[page], "Page.navigate", {"url": args.url}
                )
        elif args.action == "select":
            await page.bring_to_front()
        elif args.action == "close":
            if len(self.tabs) == 1:
                fresh = await self.context.new_page()
                await self.page_sessions[fresh]
            await page.close()
        elif args.action == "chrome":
            state = await self.navigation_state()
            page = self.tabs.get(state["activeTabId"])
            if page is None:
                raise fail("invalidTarget")
            session = await self.page_sessions[page]
            await self.set_chrome(
                session, await session.send("Browser.getWindowForTarget"), args.native_chrome
            )
        else:
            session = await self.page_sessions[page]
            if args.action == "navigate":
                await self.navigation_command(session, "Page.navigate", {"url": args.url})
            elif args.action in {"back", "forward"}:
                await session.send("Page.stopLoading")
                history = await session.send("Page.getNavigationHistory")
                index = history["currentIndex"] + (-1 if args.action == "back" else 1)
                if 0 <= index < len(history["entries"]):
                    await self.navigation_command(
                        session,
                        "Page.navigateToHistoryEntry",
                        {"entryId": history["entries"][index]["id"]},
                    )
            elif args.action == "reload":
                await self.navigation_command(session, "Page.reload", {})
            elif args.action == "stop":
                await session.send("Page.stopLoading")
                self.loading_frames[page].clear()
        return await self.navigation_state()

    async def navigation_command(self, session, method: str, params: dict) -> None:
        async def navigate():
            try:
                async with asyncio.timeout(15):
                    result = await session.send(method, params)
                return not result.get("errorText")
            except (PlaywrightError, TimeoutError):
                return False

        # A slow response must not hold the host pipe and prevent state reads or Stop.
        pending = asyncio.create_task(navigate())
        self.tasks.add(pending)

        def finished(task):
            self.tasks.discard(task)
            if not task.cancelled() and not task.result():
                self.diagnostics.append("browser.actionFailed: navigation did not complete")

        pending.add_done_callback(finished)
        done, _ = await asyncio.wait([pending], timeout=0.1)
        if done and not pending.result():
            raise fail("actionFailed")

    async def download(self, download) -> None:
        file_id = None
        try:
            if len(self.files) >= 32 or self.total_size() + MAX_FILE_BYTES > 256 * 1024 * 1024:
                await download.cancel()
                return
            file_id = str(uuid4())
            filename = download.suggested_filename.replace("\\", "_").replace("/", "_")
            filename = "".join(c for c in filename if ord(c) >= 32 and ord(c) != 127)[:255]
            self.files[file_id] = {
                "id": file_id,
                "filename": filename or "download",
                "size": MAX_FILE_BYTES,
                "ready": False,
                "download": True,
            }
            path = await download.path()
            if path is None:
                raise fail("actionFailed")
            size = path.stat().st_size
            if size > MAX_FILE_BYTES:
                raise fail("fileTooLarge")
            target = self.live / file_id
            await download.save_as(target)
            self.files[file_id].update(size=size, ready=True)
            await download.delete()
        except Exception:
            self.files.pop(file_id, None)
            with contextlib.suppress(Exception):
                await download.delete()
            self.diagnostics.append("browser.actionFailed: download was not retained")

    def total_size(self) -> int:
        return sum(entry["size"] for entry in self.files.values())

    def upload_payload(self, file_id: str) -> dict:
        entry = self.files.get(file_id)
        if not entry or not entry["ready"] or entry["download"] or entry.get("screenshot"):
            raise fail("invalidTarget")
        fd = os.open(self.live / file_id, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise fail("fileTooLarge")
        return {
            "name": entry["filename"],
            "mimeType": mimetypes.guess_type(entry["filename"])[0] or "application/octet-stream",
            "buffer": data,
        }

    async def file(self, args: BrowserFileArguments) -> dict:
        if args.action == "begin":
            if len(self.files) >= 32 or self.total_size() + args.size > 256 * 1024 * 1024:
                raise fail("fileTooLarge")
            file_id = str(uuid4())
            fd = os.open(
                self.live / file_id, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(fd, "wb"):
                pass
            self.files[file_id] = {
                "id": file_id,
                "filename": args.filename,
                "size": args.size,
                "ready": False,
                "download": False,
            }
            return {"fileId": file_id, "offset": 0, "size": args.size, "complete": False}
        entry = self.files.get(args.file_id)
        if entry is None:
            raise fail("invalidTarget")
        if args.action == "choose":
            if not self.manual:
                raise fail("busy")
            payload = self.upload_payload(args.file_id)
            for page, chooser in list(self.pending_choosers.items()):
                if page.is_closed():
                    self.pending_choosers.pop(page, None)
                    continue
                try:
                    state = await self.page_focus(await self.page_sessions[page])
                    focused = state.get("focused") and state.get("visible")
                except PlaywrightError:
                    if self.pending_choosers.get(page) is chooser:
                        self.pending_choosers.pop(page, None)
                    continue
                if not focused:
                    continue
                if self.pending_choosers.get(page) is not chooser:
                    raise fail("invalidTarget")
                try:
                    await chooser.set_files(payload, timeout=8000)
                    return {"ok": True}
                except PlaywrightError:
                    raise fail("invalidTarget") from None
                finally:
                    # Do not discard a newer chooser opened by a page's change handler.
                    if self.pending_choosers.get(page) is chooser:
                        self.pending_choosers.pop(page, None)
            raise fail("invalidTarget")
        path = self.live / args.file_id
        if args.action == "delete":
            path.unlink(missing_ok=True)
            del self.files[args.file_id]
            return {"ok": True}
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
        with os.fdopen(fd, "r+b") as handle:
            size = os.fstat(handle.fileno()).st_size
            if size > MAX_FILE_BYTES:
                raise fail("fileTooLarge")
            if args.action == "write":
                data = decode_chunk(args.data)
                if entry["ready"] or entry["download"] or args.offset != size:
                    raise fail("invalidTarget")
                if size + len(data) > entry["size"]:
                    raise fail("fileTooLarge")
                handle.seek(size)
                handle.write(data)
                size += len(data)
            elif args.action == "finish":
                if entry["download"] or entry.get("screenshot") or size != entry["size"]:
                    raise fail("invalidTarget")
                entry["ready"] = True
            elif args.action == "read":
                if (
                    not (entry["download"] or entry.get("screenshot"))
                    or not entry["ready"]
                    or args.offset > size
                    or size != entry["size"]
                ):
                    raise fail("invalidTarget")
                handle.seek(args.offset)
                data = handle.read(args.max_bytes)
                return {
                    "fileId": args.file_id,
                    "filename": entry["filename"],
                    "size": size,
                    "offset": args.offset + len(data),
                    "complete": args.offset + len(data) == size,
                    "data": base64.b64encode(data).decode("ascii"),
                }
        return {
            "fileId": args.file_id,
            "offset": size,
            "size": entry["size"],
            "complete": entry["ready"],
        }

    def discard_screenshots(self) -> None:
        for file_id, entry in list(self.files.items()):
            if entry.get("screenshot"):
                (self.live / file_id).unlink(missing_ok=True)
                del self.files[file_id]

    async def disable_translation_prompt(self) -> None:
        # Use Chromium's preference API after it owns the profile, not a Preferences file rewrite.
        async with asyncio.timeout(8):
            settings = await self.context.new_page()
            try:
                await settings.goto("chrome://settings/languages", wait_until="domcontentloaded")
                disabled = await settings.evaluate("""() => new Promise(resolve => {
                    chrome.settingsPrivate.setPref('translate.enabled', false, '', () => {
                        chrome.settingsPrivate.getPref('translate.enabled', pref => {
                            resolve(pref.value === false);
                        });
                    });
                })""")
                if disabled is not True:
                    raise fail("notReady")
            finally:
                await settings.close()

    async def screenshot(self, page, browser_id: str) -> dict:
        self.discard_screenshots()
        data = await page.screenshot(type="png", full_page=False, timeout=8000)
        with Image.open(BytesIO(data)) as image:
            width, height = image.size
            scale = min(
                1, 1568 / width, 1568 / height, (1.15 * 1024 * 1024 / (width * height)) ** 0.5
            )
            if scale < 1:
                with (
                    image.resize(
                        (max(1, int(width * scale)), max(1, int(height * scale))),
                        Image.Resampling.LANCZOS,
                    ) as resized,
                    BytesIO() as output,
                ):
                    resized.save(output, format="PNG")
                    data = output.getvalue()
        if len(data) > MAX_FILE_BYTES:
            raise fail("fileTooLarge")
        staged = await self.file(
            BrowserFileArguments(
                browserId=browser_id, action="begin", filename="screenshot.png", size=len(data)
            )
        )
        file_id = staged["fileId"]
        self.files[file_id]["screenshot"] = True
        try:
            # No awaits between allocation and publication: cancellation cannot leave a late write.
            fd = os.open(self.live / file_id, os.O_WRONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            self.files[file_id]["ready"] = True
        except BaseException:
            self.discard_screenshots()
            raise
        return {"fileId": file_id, "size": len(data)}

    async def execute(self, args: BrowserExecuteArguments) -> dict:
        if not self.context:
            raise fail("notConnected")
        if not args.allow_network:
            await self.close()
            raise fail("invalidTarget")
        action = args.action
        if not args.tab_id:
            await self.navigation_state()
        page = self.tabs.get(args.tab_id or self.active_tab_id)
        if args.tab_id and page is None:
            raise fail("invalidTarget")
        text = screenshot = result_data = None
        if action == "newtab":
            if len(self.tabs) >= 32:
                raise fail("busy")
            page = await self.context.new_page()
        if action not in {"tabs", "diagnostics"} and page is None:
            raise fail("invalidTarget")
        if page is not None and action not in {"tabs", "diagnostics"}:
            await page.bring_to_front()
        if action in {"navigate", "newtab"} and args.url:
            await page.goto(args.url, wait_until="domcontentloaded", timeout=15_000)
        elif action == "closetab":
            if len(self.tabs) == 1:
                await self.context.new_page()
            await page.close()
            await self.navigation_state()
            page = self.tabs.get(self.active_tab_id)
        elif action == "inspect":
            text, result_data = await self._inspect(page)
        elif action == "screenshot":
            screenshot = await self.screenshot(page, args.browser_id)
        elif action in {"click", "fill", "upload"}:
            # Playwright selectors are locators, never caller-controlled JavaScript.
            locator = page.locator(args.selector)
            if action == "click":
                await locator.click()
            elif action == "fill":
                await locator.fill(args.text)
            else:
                await locator.set_input_files(self.upload_payload(args.upload_id))
        elif action == "press":
            if args.selector:
                await page.locator(args.selector).press(args.key)
            else:
                await page.keyboard.press(args.key)
        elif action == "scroll":
            await page.mouse.wheel(0, -600 if args.direction == "up" else 600)
        elif action == "diagnostics":
            text = "\n".join(self.diagnostics)[:32_768]
        if args.observe and action != "inspect" and page is not None:
            try:
                text, result_data = await self._inspect(page)
            except Exception:
                # The mutation completed; a failed optional observation must not invite replay.
                text, result_data = None, {"observationFailed": True}
        tabs = []
        for tab_id, tab in list(self.tabs.items()):
            if not tab.is_closed():
                tabs.append(
                    {"id": tab_id, "url": tab.url[:4096], "title": (await tab.title())[:500]}
                )
        return BrowserActionResult(
            tabs=tabs,
            tabId=next((k for k, v in self.tabs.items() if v is page), None),
            url=page.url[:4096] if page else None,
            text=text,
            screenshot=screenshot,
            data=result_data,
            downloads=[
                {key: entry[key] for key in ("id", "filename", "size")}
                for entry in self.files.values()
                if entry["download"] and entry["ready"]
            ],
        ).model_dump(by_alias=True)

    async def _inspect(self, page) -> tuple[str, dict[str, str]]:
        document_id = self.document_ids[page]
        state = await page.locator("body").evaluate("""body => {
                const query = 'a[href],button,input,select,textarea,form,' +
                    '[role],[contenteditable=true]';
                const state = [];
                const elements = body.querySelectorAll(query);
                for (let index = 0; index < Math.min(elements.length, 80); index++) {
                    const node = elements[index];
                    const id = node.id ? '#' + CSS.escape(node.id) : '';
                    const uniqueId = id && id.length <= 1900 &&
                        document.querySelectorAll(id).length === 1;
                    const selector = uniqueId
                        ? id : 'body >> ' + query + ' >> nth=' + index;
                    const tag = node.tagName.toLowerCase();
                    const formAction = tag === 'form' ? node.action :
                        (node.hasAttribute('formaction') ? node.formAction :
                            node.form?.action) || '';
                    const href = node.getAttribute('href') === null ? '' :
                        String(node.href?.baseVal ?? node.href ?? '');
                    const value = node.isContentEditable ? node.textContent : node.value;
                    if (value?.length > 65536) return {tooLarge: true};
                    state.push({selector, value, checked: node.checked,
                        disabled: node.disabled, readOnly: node.readOnly, href, formAction,
                        formMethod: node.hasAttribute('formmethod') ? node.formMethod :
                            node.form?.method || node.method,
                        selected: node.selectedOptions ?
                            Array.from(node.selectedOptions, option => option.index) : null,
                        focused: node === document.activeElement});
                    if (JSON.stringify(state).length > 524288) return {tooLarge: true};
                }
                state.push({focusedTag: document.activeElement?.tagName,
                    focusedId: document.activeElement?.id});
                state.push({width: innerWidth, height: innerHeight,
                    screenWidth: screen.width, screenHeight: screen.height,
                    scrollX, scrollY});
                return {state};
            }""")
        if state.get("tooLarge"):
            raise fail("fileTooLarge")
        impl = page._impl_obj
        result = await impl._channel.send_return_as_dict(
            "snapshotForAI", impl._timeout_settings.timeout, {"timeout": 8000}
        )
        text = result.get("full")
        if not isinstance(text, str) or len(text) > 32_768:
            raise fail("fileTooLarge")
        if self.document_ids.get(page) != document_id:
            raise fail("invalidTarget")
        material = json.dumps(
            {
                "pageId": next(k for k, v in self.tabs.items() if v is page),
                "documentId": document_id,
                "geometry": self.geometry,
                "text": text,
                **state,
            }
        ).encode()
        return text, {
            "snapshotSha256": hmac.new(self.snapshot_key, material, hashlib.sha256).hexdigest()
        }

    async def close(self) -> None:
        closing = getattr(self, "_close_task", None)
        if closing is None:

            async def close() -> None:
                deadline = asyncio.get_running_loop().time() + 10
                self.pending_choosers.clear()
                self.document_ids.clear()
                tasks = set(self.tasks) | set(self.page_sessions.values())
                for task in tasks:
                    task.cancel()
                if self.server:
                    self.server.close()
                try:
                    async with asyncio.timeout_at(deadline):
                        # Persist the profile before waiting for cancelled page/VNC requests.
                        if self.context:
                            with contextlib.suppress(Exception):
                                await asyncio.wait_for(self.context.close(), 5)
                        if self.playwright:
                            with contextlib.suppress(Exception):
                                await asyncio.wait_for(self.playwright.stop(), 2)
                        if tasks:
                            done, _ = await asyncio.wait(tasks, timeout=2)
                            for task in done:
                                if not task.cancelled():
                                    task.exception()
                        if self.server:
                            await self.server.wait_closed()
                except TimeoutError:
                    pass
                finally:
                    if self.clipboard_owner is not None:
                        with contextlib.suppress(ProcessLookupError):
                            self.clipboard_owner.kill()
                    for process in reversed(self.processes):
                        with contextlib.suppress(ProcessLookupError):
                            process.kill()
                    with contextlib.suppress(TimeoutError):
                        async with asyncio.timeout_at(deadline):
                            await asyncio.gather(
                                *(asyncio.to_thread(process.wait) for process in self.processes)
                            )
                            if self.clipboard_owner is not None:
                                await self.clipboard_owner.wait()
                    try:
                        self.discard_screenshots()
                    finally:
                        if self.temporary:
                            self.temporary.cleanup()

            closing = self._close_task = asyncio.create_task(close())
        while not closing.done():
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(closing)
        closing.result()


async def serve(root: Path) -> None:
    if sys.platform != "linux" or os.getuid() == 0:
        raise RuntimeError("Browser worker requires a non-root Linux user")
    os.umask(0o077)
    # Browser downloads cannot grow a single file without bound, including before completion.
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_BYTES, MAX_FILE_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    worker = BrowserWorker(root)
    reader = asyncio.StreamReader(limit=1024 * 1024)
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, sys.stdin.buffer)
    current = asyncio.current_task()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, current.cancel)
    try:
        while raw := await reader.readline():
            op = None
            elapsed_ms = None
            try:
                request = json.loads(raw)
                op = request["op"]
                if op == "start":
                    result = await worker.start(
                        probe=request.get("probe", False),
                        allow_network=request.get("allowNetwork", True),
                    )
                elif op == "execute":
                    args = BrowserExecuteArguments.model_validate(request["args"])
                    started = monotonic()
                    try:
                        result = await worker.execute(args)
                    finally:
                        elapsed_ms = max(0, (monotonic() - started) * 1000)
                elif op == "navigation":
                    result = await worker.navigation(
                        BrowserNavigationArguments.model_validate(request["args"])
                    )
                elif op == "file":
                    result = await worker.file(BrowserFileArguments.model_validate(request["args"]))
                elif op == "control":
                    args = BrowserControlArguments.model_validate(request["args"])
                    result = await worker.control(args.manual)
                elif op == "clipboard":
                    result = await worker.clipboard(
                        BrowserClipboardArguments.model_validate(request["args"])
                    )
                elif op == "resize":
                    args = BrowserResizeArguments.model_validate(request["args"])
                    result = await worker.resize(args.width, args.height)
                elif op == "view":
                    result = {"socket": str(worker.live / "vnc")}
                elif op == "health":
                    result = {
                        "ok": bool(
                            worker.context
                            and worker.context.browser.is_connected()
                            and all(p.poll() is None for p in worker.processes)
                        )
                    }
                elif op == "close":
                    break
                else:
                    raise fail("invalidTarget")
                response = {"ok": True, "result": result}
            except AgentError as exc:
                response = {"ok": False, "error": exc.code}
            except Exception:
                response = {"ok": False, "error": "browser.actionFailed"}
            if not response["ok"] and op == "start":
                response["stage"] = worker.startup_stage
            if elapsed_ms is not None:
                response["elapsedMs"] = elapsed_ms
            payload = json.dumps(response, ensure_ascii=True)
            if len(payload) > 950_000:
                payload = '{"ok":false,"error":"browser.fileTooLarge"}'
            print(payload, flush=True)
    except asyncio.CancelledError:
        pass
    finally:
        await worker.close()
