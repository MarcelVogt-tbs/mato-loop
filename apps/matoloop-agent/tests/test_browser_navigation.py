"""Headed checks run as the runtime user in the existing dev agent container."""

import asyncio
import ctypes
import json
import os
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from playwright.async_api import BrowserType
from pydantic import ValidationError

from matoloop_agent.browser_contracts import (
    BrowserClipboardArguments,
    BrowserExecuteArguments,
    BrowserNavigationArguments,
)
from matoloop_agent.browser_worker import BrowserWorker
from matoloop_agent.filesystem import AgentError


class NavigationContractTests(unittest.TestCase):
    def test_strict_host_contract(self):
        browser_id, tab_id = str(uuid4()), str(uuid4())
        for action in (
            "state",
            "new",
            "chrome",
            "navigate",
            "select",
            "close",
            "back",
            "forward",
            "reload",
            "stop",
        ):
            fields = {} if action in {"state", "new", "chrome"} else {"tabId": tab_id}
            if action == "chrome":
                fields["nativeChrome"] = True
            if action == "navigate":
                fields["url"] = "about:blank"
            args = {"browserId": browser_id, "action": action, **fields}
            BrowserNavigationArguments.model_validate(args)
            for extra in ({"javascript": "x"}, {"executable": "/bin/sh"}, {"allowNetwork": True}):
                with self.assertRaises(ValidationError):
                    BrowserNavigationArguments.model_validate({**args, **extra})
        for fields in (
            {"action": "state", "tabId": None},
            {"action": "state", "nativeChrome": False},
            {"action": "select"},
            {"action": "new", "url": None},
            {"action": "chrome", "nativeChrome": 1},
            {"action": "chrome", "nativeChrome": "true"},
            *(
                {"action": "new", "url": url}
                for url in (
                    "javascript:alert(1)",
                    "file:///etc/passwd",
                    "chrome://settings",
                    "about:blank#x",
                    "https://user:secret@example.test",
                    "https://example.test:bad",
                    "https://example.test\\@evil.test",
                    " https://example.test",
                    "https://a\nb.test",
                )
            ),
        ):
            with self.assertRaises(ValidationError):
                BrowserNavigationArguments.model_validate({"browserId": browser_id, **fields})
        for action in ("chrome", "select", "back", "forward", "reload", "stop", "state"):
            with self.assertRaises(ValidationError):
                BrowserExecuteArguments(browserId=browser_id, action=action)


@unittest.skipUnless(
    os.environ.get("MATOLOOP_BROWSER_NAVIGATION_TEST") == "1", "Opt-in headed navigation test"
)
class HeadedNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_input_fence_after_viewer_disconnect(self):
        with tempfile.TemporaryDirectory(prefix="browser-input-fence-") as root:
            worker = BrowserWorker(Path(root))
            viewers = []

            async def rfb():
                reader, writer = await asyncio.open_unix_connection(worker.live / "vnc")
                viewers.append(writer)
                self.assertEqual(await reader.readexactly(12), b"RFB 003.008\n")
                writer.write(b"RFB 003.008\n")
                await writer.drain()
                count = (await reader.readexactly(1))[0]
                self.assertIn(1, await reader.readexactly(count))
                writer.write(b"\x01")
                await writer.drain()
                self.assertEqual(await reader.readexactly(4), b"\x00" * 4)
                writer.write(b"\x01")
                await writer.drain()
                init = await reader.readexactly(24)
                await reader.readexactly(int.from_bytes(init[-4:], "big"))
                writer.write(struct.pack(">BBH", 2, 0, 1) + struct.pack(">i", 0))
                writer.write(struct.pack(">BBHHHH", 3, 0, 0, 0, 1, 1))
                await writer.drain()
                update = await reader.readexactly(4)
                self.assertEqual(update[0], 0)
                for _ in range(int.from_bytes(update[2:], "big")):
                    _, _, width, height, encoding = struct.unpack(
                        ">HHHHi", await reader.readexactly(12)
                    )
                    self.assertEqual(encoding, 0)
                    await reader.readexactly(width * height * (init[4] // 8))
                return reader, writer

            async def input_mask():
                output = await worker.x_command(
                    "x11vnc",
                    "-norc",
                    "-nocmds",
                    "-display",
                    worker.environment["DISPLAY"],
                    "-auth",
                    worker.environment["XAUTHORITY"],
                    "-Q",
                    "DIRECT:pointer_mask",
                )
                # Native XQueryPointer state, not x11vnc's per-viewer input bookkeeping.
                return int(output.strip().removeprefix("aro=pointer_mask:"), 16)

            try:
                async with asyncio.timeout(45):
                    self.assertEqual((await worker.start())["state"], "ready")
                    page = worker.context.pages[0]
                    await page.set_content(
                        '<textarea style="position:absolute;inset:0;width:100%;height:100%">'
                        "retained</textarea>"
                    )
                    await page.evaluate("""() => {
                        window.fenceEvents = [];
                        for (const type of ['keydown', 'mousedown', 'mousemove'])
                            document.addEventListener(type, e => fenceEvents.push({
                                type, key:e.key, control:e.ctrlKey, buttons:e.buttons,
                                trusted:e.isTrusted
                            }));
                    }""")
                    session = await worker.context.browser.new_browser_cdp_session()
                    processes = (await session.send("SystemInfo.getProcessInfo"))["processInfo"]
                    pid = next(p["id"] for p in processes if p["type"] == "browser")
                    inode = (worker.live / "vnc").stat().st_ino

                    # Exercise both the missing-viewer gap and the existing live-viewer fence.
                    for disconnected in (True, False):
                        reader, writer = await rfb()
                        await page.evaluate("fenceEvents.length = 0")
                        writer.write(struct.pack(">BBHI", 4, 1, 0, 0xFFE3))  # Control_L down.
                        writer.write(struct.pack(">BBHH", 5, 1, 30, 30))  # Button 1 down.
                        await writer.drain()
                        await page.wait_for_function(
                            "fenceEvents.some(e => e.type === 'mousedown' && e.control "
                            "&& e.buttons === 1 && e.trusted)"
                        )
                        self.assertEqual(await input_mask(), 0x104)  # ControlMask | Button1Mask.
                        if disconnected:
                            tasks = list(worker.vnc_sessions)
                            writer.close()  # No key-up or pointer-up RFB messages.
                            await writer.wait_closed()
                            await asyncio.gather(*tasks)
                            self.assertFalse(worker.vnc_sessions)
                            self.assertEqual(await input_mask(), 0x104)

                        self.assertEqual(await worker.control(False), {"ok": True})
                        self.assertEqual(await input_mask(), 0)
                        self.assertEqual(await worker.control(True), {"ok": True})
                        self.assertEqual(await reader.read(), b"")
                        self.assertEqual(await input_mask(), 0)
                        _, next_writer = await rfb()
                        await page.evaluate("fenceEvents.length = 0")
                        next_writer.write(struct.pack(">BBHH", 5, 0, 60, 60))
                        next_writer.write(
                            struct.pack(">BBHI", 4, 1, 0, ord("x"))
                            + struct.pack(">BBHI", 4, 0, 0, ord("x"))
                        )
                        await next_writer.drain()
                        await page.wait_for_function(
                            "fenceEvents.some(e => e.type === 'keydown' && e.key === 'x' "
                            "&& !e.control && e.trusted) && fenceEvents.some(e => "
                            "e.type === 'mousemove' && e.buttons === 0 && !e.control && e.trusted)"
                        )
                        tasks = list(worker.vnc_sessions)
                        next_writer.close()
                        await next_writer.wait_closed()
                        await asyncio.gather(*tasks)

                    processes = (await session.send("SystemInfo.getProcessInfo"))["processInfo"]
                    self.assertEqual(
                        next(p["id"] for p in processes if p["type"] == "browser"), pid
                    )
                    self.assertEqual((worker.live / "vnc").stat().st_ino, inode)
                    self.assertIn("retained", await page.locator("textarea").input_value())
                    print(
                        f"INPUT FENCE PASS PID {pid}: held Control+button, disconnected/live "
                        "viewer, native mask 0x104 -> 0, clean new viewer, same browser/document",
                        flush=True,
                    )
            finally:
                for writer in viewers:
                    writer.close()
                await worker.close()

    async def test_fullscreen_desktop_overlays(self):
        """Capture the X root, not CDP page screenshots which omit native overlays."""
        artifacts = Path(os.environ.get("MATOLOOP_BROWSER_SCREENSHOTS", "/tmp/browser-navigation"))
        artifacts.mkdir(parents=True, exist_ok=True)
        launch = BrowserType.launch_persistent_context
        evidence = {}

        for name, flags in (
            ("baseline", []),
            ("test-type", ["--test-type"]),
            ("kiosk", ["--kiosk"]),
            ("force-app-mode", ["--force-app-mode"]),
        ):

            async def launch_with_flags(browser_type, *args, flags=flags, **kwargs):
                kwargs["args"] = [
                    arg for arg in kwargs["args"] if arg not in {"--kiosk", "--force-app-mode"}
                ] + flags
                return await launch(browser_type, *args, **kwargs)

            with tempfile.TemporaryDirectory() as root:
                worker = BrowserWorker(Path(root))
                try:
                    with patch.object(BrowserType, "launch_persistent_context", launch_with_flags):
                        self.assertEqual((await worker.start())["state"], "ready")
                    page = worker.context.pages[0]
                    session = await worker.context.new_cdp_session(page)
                    window = await session.send("Browser.getWindowForTarget")

                    class XImage(ctypes.Structure):
                        _fields_ = [
                            ("width", ctypes.c_int),
                            ("height", ctypes.c_int),
                            ("xoffset", ctypes.c_int),
                            ("format", ctypes.c_int),
                            ("data", ctypes.c_void_p),
                            ("byte_order", ctypes.c_int),
                            ("bitmap_unit", ctypes.c_int),
                            ("bitmap_bit_order", ctypes.c_int),
                            ("bitmap_pad", ctypes.c_int),
                            ("depth", ctypes.c_int),
                            ("bytes_per_line", ctypes.c_int),
                            ("bits_per_pixel", ctypes.c_int),
                            ("red_mask", ctypes.c_ulong),
                            ("green_mask", ctypes.c_ulong),
                            ("blue_mask", ctypes.c_ulong),
                        ]

                    x11 = ctypes.CDLL("libX11.so.6")
                    pointer, integer, ulong = ctypes.c_void_p, ctypes.c_int, ctypes.c_ulong
                    for function, arguments, result in (
                        ("XOpenDisplay", [ctypes.c_char_p], pointer),
                        ("XCloseDisplay", [pointer], integer),
                        ("XDefaultRootWindow", [pointer], ulong),
                        (
                            "XGetImage",
                            [
                                pointer,
                                ulong,
                                integer,
                                integer,
                                ctypes.c_uint,
                                ctypes.c_uint,
                                ulong,
                                integer,
                            ],
                            ctypes.POINTER(XImage),
                        ),
                        ("XDestroyImage", [ctypes.POINTER(XImage)], integer),
                        (
                            "XWarpPointer",
                            [
                                pointer,
                                ulong,
                                ulong,
                                integer,
                                integer,
                                ctypes.c_uint,
                                ctypes.c_uint,
                                integer,
                                integer,
                            ],
                            integer,
                        ),
                        ("XSync", [pointer, integer], integer),
                        ("XKeysymToKeycode", [pointer, ulong], ctypes.c_ubyte),
                    ):
                        getattr(x11, function).argtypes = arguments
                        getattr(x11, function).restype = result
                    previous = os.environ.get("XAUTHORITY")
                    os.environ["XAUTHORITY"] = worker.environment["XAUTHORITY"]
                    try:
                        display = x11.XOpenDisplay(worker.environment["DISPLAY"].encode())
                    finally:
                        if previous is None:
                            os.environ.pop("XAUTHORITY", None)
                        else:
                            os.environ["XAUTHORITY"] = previous
                    self.assertTrue(display)
                    desktop = x11.XDefaultRootWindow(display)
                    xtest = ctypes.CDLL("libXtst.so.6")
                    xtest.XTestFakeKeyEvent.argtypes = [pointer, ctypes.c_uint, integer, ulong]
                    xtest.XTestFakeKeyEvent.restype = integer
                    xtest.XTestFakeButtonEvent.argtypes = [pointer, ctypes.c_uint, integer, ulong]
                    xtest.XTestFakeButtonEvent.restype = integer

                    def keys(*symbols, x11=x11, xtest=xtest, display=display):
                        codes = [x11.XKeysymToKeycode(display, symbol) for symbol in symbols]
                        for code in codes:
                            self.assertTrue(xtest.XTestFakeKeyEvent(display, code, 1, 0))
                        for code in reversed(codes):
                            self.assertTrue(xtest.XTestFakeKeyEvent(display, code, 0, 0))
                        x11.XSync(display, False)

                    def capture(
                        label,
                        expected=None,
                        name=name,
                        worker=worker,
                        x11=x11,
                        display=display,
                        desktop=desktop,
                    ):
                        width, height = worker.geometry["width"], worker.geometry["height"]
                        image = x11.XGetImage(
                            display, desktop, 0, 0, width, height, ctypes.c_ulong(-1), 2
                        )
                        self.assertTrue(image)
                        try:
                            info = image.contents
                            self.assertEqual((info.bits_per_pixel, info.byte_order), (32, 0))
                            self.assertEqual(
                                (info.red_mask, info.green_mask, info.blue_mask),
                                (0xFF0000, 0xFF00, 0xFF),
                            )
                            raw = ctypes.string_at(info.data, info.bytes_per_line * height)
                            rows = []
                            different = 0
                            for y in range(height):
                                row = raw[
                                    y * info.bytes_per_line : y * info.bytes_per_line + width * 4
                                ]
                                rgb = bytearray(width * 3)
                                rgb[0::3], rgb[1::3], rgb[2::3] = row[2::4], row[1::4], row[0::4]
                                rows.append(b"\0" + rgb)
                                if expected:
                                    different += width - rgb.count(expected)

                            def chunk(kind, data):
                                return (
                                    struct.pack(">I", len(data))
                                    + kind
                                    + data
                                    + struct.pack(">I", zlib.crc32(kind + data))
                                )

                            png = b"\x89PNG\r\n\x1a\n" + chunk(
                                b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
                            )
                            png += chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(
                                b"IEND", b""
                            )
                            (artifacts / f"{name}-{label}.png").write_bytes(png)
                            return different
                        finally:
                            x11.XDestroyImage(image)

                    try:
                        await page.set_content("<style>html{background:#246840}</style>")
                        await page.evaluate("new Promise(requestAnimationFrame)")
                        x11.XWarpPointer(display, 0, desktop, 0, 0, 0, 0, 20, 400)
                        x11.XSync(display, False)
                        samples = []
                        for index in range(5):
                            samples.append(capture(f"entry-{index}", b"\x24\x68\x40"))
                            await asyncio.sleep(0.1)
                        evidence[name] = {"entryOverlayPixels": max(samples)}
                        # Isolate the top-edge control from the entry toast, which suppresses it.
                        # This is a test wait only; the actual view is never delayed or hidden.
                        await asyncio.sleep(4)
                        x11.XWarpPointer(display, 0, desktop, 0, 0, 0, 0, 20, 400)
                        x11.XSync(display, False)
                        await asyncio.sleep(0.05)
                        x11.XWarpPointer(display, 0, desktop, 0, 0, 0, 0, 640, 0)
                        x11.XSync(display, False)
                        await asyncio.sleep(0.4)
                        evidence[name]["topEdgeOverlayPixels"] = capture(
                            "top-edge", b"\x24\x68\x40"
                        )
                        await worker.set_chrome(session, window, True)
                        await asyncio.sleep(0.3)
                        capture("native-chrome")
                        evidence[name]["nativeChromeSize"] = await page.evaluate(
                            "[innerWidth,innerHeight]"
                        )
                        self.assertLess(evidence[name]["nativeChromeSize"][1], 800)
                        await page.mouse.click(20, 400)
                        devtools_keys = (0xFFC9,)
                        keys(*devtools_keys)
                        browser_session = await worker.context.browser.new_browser_cdp_session()
                        try:
                            devtools = False
                            for _ in range(20):
                                targets = (await browser_session.send("Target.getTargets"))[
                                    "targetInfos"
                                ]
                                devtools = any(t["url"].startswith("devtools://") for t in targets)
                                if devtools:
                                    break
                                await asyncio.sleep(0.1)
                            evidence[name]["nativeF12"] = devtools
                            if name == "force-app-mode" and not devtools:
                                devtools_keys = (0xFFE3, 0xFFE1, ord("i"))
                                keys(*devtools_keys)
                                for _ in range(20):
                                    targets = (await browser_session.send("Target.getTargets"))[
                                        "targetInfos"
                                    ]
                                    devtools = any(
                                        t["url"].startswith("devtools://") for t in targets
                                    )
                                    if devtools:
                                        break
                                    await asyncio.sleep(0.1)
                            evidence[name]["nativeDevTools"] = devtools
                            if devtools:
                                async with asyncio.timeout(5):
                                    while (
                                        capture("devtools-attempt", b"\x24\x68\x40")
                                        <= 1280 * 87 + 1000
                                    ):
                                        await asyncio.sleep(0.05)
                            else:
                                capture("devtools-attempt")
                            if devtools:
                                keys(*devtools_keys)
                                async with asyncio.timeout(5):
                                    while any(
                                        t["url"].startswith("devtools://")
                                        for t in (await browser_session.send("Target.getTargets"))[
                                            "targetInfos"
                                        ]
                                    ):
                                        await asyncio.sleep(0.05)
                        finally:
                            await browser_session.detach()
                        await worker.set_chrome(
                            session, await session.send("Browser.getWindowForTarget"), False
                        )
                        await asyncio.sleep(0.3)
                        evidence[name]["reentryOverlayPixels"] = capture("reentry", b"\x24\x68\x40")
                        if name == "force-app-mode":
                            await worker.set_chrome(
                                session, await session.send("Browser.getWindowForTarget"), True
                            )
                            await asyncio.sleep(0.2)
                            keys(0xFFE3, ord("l"))
                            await asyncio.sleep(0.15)
                            evidence[name]["nativeCtrlL"] = not (await worker.page_focus(session))[
                                "focused"
                            ]
                            # Click the real native omnibox, not a page coordinate or CDP navigate.
                            x11.XWarpPointer(display, 0, desktop, 0, 0, 0, 0, 300, 62)
                            for down in (1, 0):
                                xtest.XTestFakeButtonEvent(display, 1, down, 0)
                            x11.XSync(display, False)
                            browser_id = str(uuid4())
                            await worker.clipboard(
                                BrowserClipboardArguments(
                                    browserId=browser_id, action="write", text="chrome://settings"
                                )
                            )
                            keys(0xFFE3, ord("a"))
                            keys(0xFFE3, ord("v"))
                            keys(0xFF0D)
                            for _ in range(20):
                                if page.url.startswith("chrome://settings"):
                                    break
                                await asyncio.sleep(0.1)
                            evidence[name]["nativeAddressSettings"] = page.url.startswith(
                                "chrome://settings"
                            )
                            if evidence[name]["nativeAddressSettings"]:
                                await page.wait_for_load_state()
                            capture("settings-address")
                            before = len(worker.context.pages)
                            keys(0xFFE3, ord("t"))
                            await asyncio.sleep(0.2)
                            evidence[name]["nativeCtrlT"] = len(worker.context.pages) > before
                            browser_id = str(uuid4())
                            state = await worker.navigation(
                                BrowserNavigationArguments(browserId=browser_id, action="new")
                            )
                            evidence[name]["hostNew"] = len(state["tabs"]) == before + 1
                            tab_id = next(
                                key for key, value in worker.tabs.items() if value is page
                            )
                            state = await worker.navigation(
                                BrowserNavigationArguments(
                                    browserId=browser_id, action="select", tabId=tab_id
                                )
                            )
                            evidence[name]["hostSelect"] = state["activeTabId"] == tab_id

                            async def nav(action, worker=worker, browser_id=browser_id, **fields):
                                return await worker.navigation(
                                    BrowserNavigationArguments(
                                        browserId=browser_id, action=action, **fields
                                    )
                                )

                            fixture = "http://127.0.0.1:39991/"
                            await worker.context.route(
                                fixture + "**",
                                lambda route: route.fulfill(
                                    content_type="text/html",
                                    body=(
                                        "<title>Forced app probe</title>"
                                        '<input id="value" value="retained">'
                                        "<button id=\"popup\" onclick=\"window.open('/popup','',"
                                        "'width=500,height=400')\">Popup</button>"
                                        '<button id="permission" '
                                        'onclick="navigator.clipboard.readText()">'
                                        "Clipboard permission</button>"
                                    ),
                                ),
                            )
                            await nav("navigate", tabId=tab_id, url=fixture + "one")
                            await page.wait_for_url(fixture + "one")
                            await nav("navigate", tabId=tab_id, url=fixture + "two")
                            await page.wait_for_url(fixture + "two")
                            await nav("back", tabId=tab_id)
                            await page.wait_for_url(fixture + "one")
                            await nav("forward", tabId=tab_id)
                            await page.wait_for_url(fixture + "two")
                            await nav("reload", tabId=tab_id)
                            await page.wait_for_load_state()
                            evidence[name]["hostHistoryReload"] = page.url == fixture + "two"
                            await page.locator("#popup").click()
                            for _ in range(20):
                                state = await nav("state")
                                if len(state["tabs"]) > before + 1:
                                    break
                                await asyncio.sleep(0.05)
                            evidence[name]["popup"] = len(state["tabs"]) == before + 2
                            popup_id = state["activeTabId"]
                            if popup_id != tab_id:
                                await nav("close", tabId=popup_id)
                            await nav("select", tabId=tab_id)
                            await page.locator("#permission").click()
                            await asyncio.sleep(0.3)
                            capture("permission-prompt")
                            x11.XWarpPointer(display, 0, desktop, 0, 0, 0, 0, 180, 62)
                            for down in (1, 0):
                                xtest.XTestFakeButtonEvent(display, 1, down, 0)
                            x11.XSync(display, False)
                            await asyncio.sleep(0.2)
                            capture("permission-dialog")
                            print(
                                "FORCED APP FOCUSED PROOF " + json.dumps(evidence[name]), flush=True
                            )
                    finally:
                        x11.XCloseDisplay(display)
                        await session.detach()
                finally:
                    await worker.close()
        print("X DESKTOP OVERLAY PROOF " + json.dumps(evidence), flush=True)
        for name in ("baseline", "test-type"):
            self.assertGreater(evidence[name]["entryOverlayPixels"], 1000)
            self.assertGreater(evidence[name]["topEdgeOverlayPixels"], 1000)
            self.assertTrue(evidence[name]["nativeDevTools"])
        for field in ("entryOverlayPixels", "topEdgeOverlayPixels", "reentryOverlayPixels"):
            self.assertEqual(evidence["kiosk"][field], 0)
        self.assertFalse(evidence["kiosk"]["nativeDevTools"])
        for field in ("entryOverlayPixels", "topEdgeOverlayPixels", "reentryOverlayPixels"):
            self.assertEqual(evidence["force-app-mode"][field], 0)
        for field in ("nativeF12", "nativeCtrlL", "nativeCtrlT"):
            self.assertFalse(evidence["force-app-mode"][field])
        for field in (
            "nativeDevTools",
            "nativeAddressSettings",
            "hostNew",
            "hostSelect",
            "hostHistoryReload",
            "popup",
        ):
            self.assertTrue(evidence["force-app-mode"][field])

    async def test_navigation_same_runtime(self):
        with tempfile.TemporaryDirectory() as root:
            worker = BrowserWorker(Path(root))
            browser_id = str(uuid4())
            requests = []
            slow = asyncio.Event()

            async def http(reader, writer):
                try:
                    request = await reader.readuntil(b"\r\n\r\n")
                    path = request.split(b" ")[1].decode()
                    requests.append(path)
                    if path == "/slow":
                        await slow.wait()
                    if path == "/redirect":
                        writer.write(
                            b"HTTP/1.1 302 Found\r\nLocation: /two\r\nContent-Length: 0\r\n\r\n"
                        )
                    else:
                        body = (
                            "<!doctype html><title>" + path + '</title><input id="value">'
                            "<button id=\"spa\" onclick=\"history.pushState({},'','/spa');"
                            "document.title='SPA'\">SPA</button>"
                            "<button id=\"popup\" onclick=\"window.open('/popup','',"
                            "'width=500,height=400')\">"
                            "Popup</button>"
                            "<script>document.hasFocus=()=>true;</script>"
                        ).encode()
                        writer.write(
                            b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: "
                            + str(len(body)).encode()
                            + b"\r\nConnection: close\r\n\r\n"
                            + body
                        )
                    await writer.drain()
                except (ConnectionError, asyncio.IncompleteReadError):
                    pass
                finally:
                    writer.close()
                    await writer.wait_closed()

            server = await asyncio.start_server(http, "127.0.0.1", 0)
            url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"

            async def nav(action="state", **fields):
                async with asyncio.timeout(5):
                    return await worker.navigation(
                        BrowserNavigationArguments(browserId=browser_id, action=action, **fields)
                    )

            async def until(predicate):
                state = None
                try:
                    async with asyncio.timeout(10):
                        while True:
                            state = await nav()
                            if predicate(state):
                                return state
                            await asyncio.sleep(0.05)
                except TimeoutError:
                    self.fail(f"Navigation state did not converge: {state}")

            async def native_keys(*keys, click=None):
                x11, xtest = ctypes.CDLL("libX11.so.6"), ctypes.CDLL("libXtst.so.6")
                pointer, integer, ulong = ctypes.c_void_p, ctypes.c_int, ctypes.c_ulong
                for library, name, args, result in (
                    (x11, "XOpenDisplay", [ctypes.c_char_p], pointer),
                    (x11, "XCloseDisplay", [pointer], integer),
                    (x11, "XKeysymToKeycode", [pointer, ulong], ctypes.c_ubyte),
                    (x11, "XDefaultRootWindow", [pointer], ulong),
                    (
                        x11,
                        "XWarpPointer",
                        [
                            pointer,
                            ulong,
                            ulong,
                            integer,
                            integer,
                            ctypes.c_uint,
                            ctypes.c_uint,
                            integer,
                            integer,
                        ],
                        integer,
                    ),
                    (x11, "XSync", [pointer, integer], integer),
                    (xtest, "XTestFakeKeyEvent", [pointer, ctypes.c_uint, integer, ulong], integer),
                    (
                        xtest,
                        "XTestFakeButtonEvent",
                        [pointer, ctypes.c_uint, integer, ulong],
                        integer,
                    ),
                ):
                    function = getattr(library, name)
                    function.argtypes, function.restype = args, result
                previous = os.environ.get("XAUTHORITY")
                os.environ["XAUTHORITY"] = worker.environment["XAUTHORITY"]
                try:
                    display = x11.XOpenDisplay(worker.environment["DISPLAY"].encode())
                finally:
                    if previous is None:
                        os.environ.pop("XAUTHORITY", None)
                    else:
                        os.environ["XAUTHORITY"] = previous
                self.assertTrue(display)
                try:
                    if click is not None:
                        x11.XWarpPointer(
                            display, 0, x11.XDefaultRootWindow(display), 0, 0, 0, 0, *click
                        )
                        for down in (1, 0):
                            xtest.XTestFakeButtonEvent(display, 1, down, 0)
                    codes = [x11.XKeysymToKeycode(display, key) for key in keys]
                    for code in codes:
                        xtest.XTestFakeKeyEvent(display, code, 1, 0)
                    for code in reversed(codes):
                        xtest.XTestFakeKeyEvent(display, code, 0, 0)
                    x11.XSync(display, False)
                finally:
                    x11.XCloseDisplay(display)
                await asyncio.sleep(0.2)

            try:
                self.assertEqual((await worker.start())["state"], "ready")
                state = await nav()
                first = state["activeTabId"]
                page = worker.tabs[first]
                socket = worker.live / "vnc"
                inode = socket.stat().st_ino
                browser_session = await worker.context.browser.new_browser_cdp_session()
                launch_args = (await browser_session.send("Browser.getBrowserCommandLine"))[
                    "arguments"
                ]
                self.assertIn("--force-app-mode", launch_args)
                self.assertNotIn("--kiosk", launch_args)
                processes = (await browser_session.send("SystemInfo.getProcessInfo"))["processInfo"]
                pid = next(p["id"] for p in processes if p["type"] == "browser")
                self.assertFalse(state["nativeChrome"])
                await nav("navigate", tabId=first, url=url + "/one")
                await until(lambda s: s["tabs"][0]["title"] == "/one" and not s["loading"])
                await page.locator("#value").fill("PRIVATE RETAINED VALUE")
                for width, height in ((320, 240), (1001, 701), (1280, 800)):
                    await worker.resize(width, height)
                    await asyncio.sleep(0.2)
                    size = await page.evaluate(
                        "[innerWidth,innerHeight,screen.width,screen.height]"
                    )
                    self.assertEqual(size, [width, height, width, height])
                await nav("new", url=url + "/two")
                state = await until(
                    lambda s: len(s["tabs"]) == 2 and s["activeTabId"] != first and not s["loading"]
                )
                second = state["activeTabId"]
                self.assertEqual(
                    (
                        await worker.execute(
                            BrowserExecuteArguments(browserId=browser_id, action="inspect")
                        )
                    )["tabId"],
                    second,
                )
                await nav("select", tabId=first)
                await until(lambda s: s["activeTabId"] == first)
                self.assertEqual(
                    await page.locator("#value").input_value(), "PRIVATE RETAINED VALUE"
                )
                self.assertNotIn("PRIVATE RETAINED VALUE", json.dumps(await nav()))
                await nav("chrome", nativeChrome=True)
                await until(lambda s: s["nativeChrome"])
                await page.wait_for_function("innerWidth === 1280 && innerHeight === 713")
                # Chromium retains compact native tabs after the mobile resize above.
                await native_keys(click=(54, 20))  # Click the second tab's visible favicon.
                await until(lambda s: s["activeTabId"] == second)
                await native_keys(click=(300, 62))  # Native omnibox; Ctrl+L belongs to the host UI.
                await until(lambda s: s["activeTabId"] == second)
                self.assertFalse(
                    (await worker.page_focus(await worker.page_sessions[worker.tabs[second]]))[
                        "focused"
                    ]
                )
                for _ in range(3):
                    self.assertEqual((await nav())["activeTabId"], second)
                self.assertFalse(
                    (await worker.page_focus(await worker.page_sessions[worker.tabs[second]]))[
                        "focused"
                    ]
                )
                await native_keys(0xFF1B)
                await nav("chrome", nativeChrome=False)
                await until(lambda s: not s["nativeChrome"])
                await nav("chrome", nativeChrome=True)
                await until(lambda s: s["nativeChrome"])
                await native_keys(0xFFE3, 0xFFE1, ord("i"))  # Native DevTools' allowed shortcut.
                async with asyncio.timeout(5):
                    while not any(
                        target["url"].startswith("devtools://")
                        for target in (await browser_session.send("Target.getTargets"))[
                            "targetInfos"
                        ]
                    ):
                        await asyncio.sleep(0.05)
                self.assertTrue((await nav())["nativeChrome"])
                await native_keys(0xFFE3, 0xFFE1, ord("i"))
                await native_keys(click=(300, 62))
                await worker.clipboard(
                    BrowserClipboardArguments(
                        browserId=browser_id, action="write", text="chrome://settings"
                    )
                )
                await native_keys(0xFFE3, ord("a"))
                await native_keys(0xFFE3, ord("v"))
                await native_keys(0xFF0D)
                await until(
                    lambda s: any(t["url"].startswith("chrome://settings") for t in s["tabs"])
                )
                self.assertTrue((await nav())["nativeChrome"])
                await nav("select", tabId=first)
                for show in (False, True, False):
                    await nav("chrome", nativeChrome=show)
                    await until(lambda s, show=show: s["nativeChrome"] == show)
                    await asyncio.sleep(0.2)
                    size = await page.evaluate("[innerHeight,screen.height]")
                    self.assertEqual(size[0] < size[1], show)
                    self.assertEqual(
                        await page.locator("#value").input_value(), "PRIVATE RETAINED VALUE"
                    )
                await page.locator("#spa").click()
                state = await until(lambda s: s["tabs"][0]["title"] == "SPA")
                self.assertTrue(state["tabs"][0]["url"].endswith("/spa"))
                self.assertTrue(state["canGoBack"])
                await nav("back", tabId=first)
                await until(lambda s: s["tabs"][0]["url"].endswith("/one") and s["canGoForward"])
                await nav("forward", tabId=first)
                await until(
                    lambda s: s["tabs"][0]["url"].endswith("/spa") and not s["canGoForward"]
                )
                self.assertEqual(
                    await page.locator("#value").input_value(), "PRIVATE RETAINED VALUE"
                )
                before = requests.count("/spa")
                await nav("reload", tabId=first)
                await until(lambda s: requests.count("/spa") > before and not s["loading"])
                await nav("navigate", tabId=first, url=url + "/redirect")
                await until(lambda s: s["tabs"][0]["url"].endswith("/two") and not s["loading"])
                await nav("navigate", tabId=first, url=url + "/slow")
                await until(lambda s: s["loading"] and s["tabs"][0]["loading"])
                await nav("stop", tabId=first)
                await until(lambda s: not s["loading"])
                slow.set()
                await page.locator("#popup").click()
                state = await until(
                    lambda s: len(s["tabs"]) == 3 and s["activeTabId"] not in {first, second}
                )
                popup_id = state["activeTabId"]
                popup = worker.tabs[popup_id]
                await asyncio.sleep(0.3)
                self.assertEqual(await popup.evaluate("[innerWidth,innerHeight]"), [1280, 800])
                await nav("close", tabId=popup_id)
                await until(lambda s: len(s["tabs"]) == 2)
                await worker.control(False)
                await nav()
                with self.assertRaises(AgentError) as error:
                    await nav("new")
                self.assertEqual(error.exception.code, "browser.busy")
                await worker.control(True)
                with self.assertRaises(AgentError):
                    await nav("close", tabId=str(uuid4()))
                for tab_id in (first, second):
                    await nav("close", tabId=tab_id)
                state = await until(
                    lambda s: len(s["tabs"]) == 1 and s["tabs"][0]["url"] == "about:blank"
                )
                self.assertNotIn(state["activeTabId"], {first, second})
                for _ in range(31):
                    added = await worker.context.new_page()
                    await worker.page_sessions[added]
                self.assertEqual(len((await nav())["tabs"]), 32)
                with self.assertRaises(AgentError) as error:
                    await nav("new")
                self.assertEqual(error.exception.code, "browser.busy")
                excess = await worker.context.new_page()
                async with asyncio.timeout(5):
                    while not excess.is_closed():
                        await asyncio.sleep(0.05)
                self.assertEqual(len((await nav())["tabs"]), 32)
                self.assertEqual(socket.stat().st_ino, inode)
                processes = (await browser_session.send("SystemInfo.getProcessInfo"))["processInfo"]
                self.assertEqual(next(p["id"] for p in processes if p["type"] == "browser"), pid)
                print(
                    f"NAVIGATION PROOF PID {pid}: native tab/omnibox clicks, chrome toggle, "
                    "Ctrl+Shift+I DevTools, Settings, resize, "
                    "SPA/history/reload/stop/redirect/popup, last-tab blank, "
                    "same VNC/profile, retained value",
                    flush=True,
                )
                await browser_session.detach()
            finally:
                slow.set()
                server.close()
                await server.wait_closed()
                await worker.close()

    async def test_native_chrome_probe(self):
        with tempfile.TemporaryDirectory() as root:
            worker = BrowserWorker(Path(root))
            try:
                self.assertEqual((await worker.start())["state"], "ready")
                page = worker.context.pages[0]
                await page.set_content('<input id="value" value="retained">')
                session = await worker.context.new_cdp_session(page)
                browser_session = await worker.context.browser.new_browser_cdp_session()
                try:
                    processes = await browser_session.send("SystemInfo.getProcessInfo")
                    window = await session.send("Browser.getWindowForTarget")
                    socket = worker.live / "vnc"
                    evidence = {}
                    await session.send(
                        "Browser.setWindowBounds",
                        {"windowId": window["windowId"], "bounds": {"windowState": "normal"}},
                    )
                    for state in ("maximized", "fullscreen", "normal", "maximized"):
                        await session.send(
                            "Browser.setWindowBounds",
                            {"windowId": window["windowId"], "bounds": {"windowState": state}},
                        )
                        await asyncio.sleep(0.6)
                        size = await page.evaluate(
                            "({width:innerWidth,height:innerHeight,"
                            "screenWidth:screen.width,screenHeight:screen.height})"
                        )
                        evidence[state] = size
                        if state == "fullscreen":
                            self.assertEqual(size["width"], size["screenWidth"])
                            self.assertEqual(size["height"], size["screenHeight"])
                        else:
                            self.assertLess(size["height"], size["screenHeight"])
                        self.assertEqual(await page.locator("#value").input_value(), "retained")
                        self.assertTrue(socket.exists())
                    after = await browser_session.send("SystemInfo.getProcessInfo")
                    pid = next(p["id"] for p in processes["processInfo"] if p["type"] == "browser")
                    self.assertIn(
                        pid, [p["id"] for p in after["processInfo"] if p["type"] == "browser"]
                    )
                    print("NATIVE CHROME PROOF " + json.dumps({"pid": pid, **evidence}), flush=True)
                finally:
                    await browser_session.detach()
                    await session.detach()
            finally:
                await worker.close()
