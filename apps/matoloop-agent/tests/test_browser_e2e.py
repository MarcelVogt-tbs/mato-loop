"""Opt-in real Quasar/browser E2E, never unittest discovery; bounded to 350 seconds.

Run from the repository root using only the development Compose helper:

    source ./scripts/dev.sh help >/dev/null
    seed=$(compose exec -T backend uv run --no-sync python \
      /workspace/apps/matoloop-agent/tests/test_browser_e2e.py seed)
    trap 'compose exec -T backend uv run --no-sync python \
      /workspace/apps/matoloop-agent/tests/test_browser_e2e.py cleanup <<<"$seed"' EXIT
    compose exec -T --user matoloop-browser matoloop-agent \
      /opt/matoloop-agent/venv/bin/python \
      /source/apps/matoloop-agent/tests/test_browser_e2e.py run <<<"$seed"

Screenshots and sanitized evidence remain in the agent's /tmp/browser-e2e-<user ID>.
Copy them with `compose cp matoloop-agent:/tmp/browser-e2e-<ID> /tmp/opencode/`.
Only this run's user is deleted (including FK-cascaded records). No LLM is called,
no existing agent config is loaded, and all browser profiles are temporary.
The inner deadline leaves time for worker cleanup. Regressions fail without UI repairs.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4


async def database(mode: str) -> None:
    from mato_loop_backend.db.session import AsyncSessionFactory
    from mato_loop_backend.models import (
        ChatSession,
        ExecutionAgent,
        LoginSession,
        SettingRecord,
        UserAccount,
    )
    from mato_loop_backend.security import hash_password, verify_password
    from sqlalchemy import delete, func, select

    async with AsyncSessionFactory() as db:
        if mode == "seed":
            user_id, session_id, second_session_id = uuid4(), uuid4(), uuid4()
            username, password = f"browser-e2e-{user_id}", secrets.token_urlsafe(32)
            db.add(
                UserAccount(
                    id=user_id,
                    username=username,
                    password_hash=hash_password(password),
                    is_admin=False,
                    is_active=True,
                )
            )
            await db.flush()
            for identifier, title in (
                (session_id, "Browser UI smoke A"),
                (second_session_id, "Browser UI smoke B"),
            ):
                db.add(
                    ChatSession(
                        id=identifier,
                        user_id=user_id,
                        title=title,
                        title_generation_status="skipped",
                        memory_mode="disabled",
                        web_search_policy="disabled",
                        control_center={
                            "tabs": [
                                {"app_id": "session", "pinned": False},
                                {"app_id": "browser", "pinned": False},
                            ],
                            "active_app_id": "browser",
                            "open": True,
                        },
                    )
                )
            await db.commit()
            print(
                json.dumps(
                    dict(
                        userId=str(user_id),
                        sessionId=str(session_id),
                        secondSessionId=str(second_session_id),
                        username=username,
                        password=password,
                    )
                )
            )
        else:
            seed = json.load(sys.stdin)
            user_id = UUID(seed["userId"])
            user = await db.get(UserAccount, user_id)
            assert user is not None and user.username == f"browser-e2e-{user_id}"
            assert not user.is_admin and verify_password(seed["password"], user.password_hash)
            await db.execute(delete(UserAccount).where(UserAccount.id == user_id))
            await db.commit()
            for model in (ChatSession, ExecutionAgent, LoginSession, SettingRecord):
                assert (
                    await db.scalar(
                        select(func.count()).select_from(model).where(model.user_id == user_id)
                    )
                    == 0
                ), f"Cleanup left {model.__name__} records"
            assert await db.get(UserAccount, user_id) is None
            print(f"CLEANUP PASS: user {user_id} and all owned test records removed")


async def smoke(seed: dict, *, scenario=None) -> None:
    from playwright.async_api import async_playwright, expect

    from matoloop_agent.config import AgentConfig
    from matoloop_agent.connection import MatoLoopAgent

    started = asyncio.get_running_loop().time()
    assert os.getuid() != 0, "Run as matoloop-browser: Chromium sandbox must stay enabled"
    assert shutil.which("xrandr") and os.access(shutil.which("xrandr"), os.X_OK), (
        "Rebuild the development service with executable xrandr before running E2E"
    )
    expect.set_options(timeout=30000)
    frontend = os.environ.get("BROWSER_E2E_FRONTEND", "http://frontend:10020")
    backend = os.environ.get("BROWSER_E2E_BACKEND", "ws://backend:6020")
    artifacts = Path("/tmp") / f"browser-e2e-{UUID(seed['userId'])}"
    artifacts.mkdir(mode=0o700, exist_ok=False)
    evidence = {
        "userId": seed["userId"],
        "cases": [],
        "errors": [],
        "websockets": [],
        "clipboardRequests": [],
        "fixtureLoads": 0,
        "fixtureRequests": [],
        "agentOperations": [],
    }
    print(f"ARTIFACTS: {artifacts}", flush=True)

    async def forward(reader, writer):
        remote_reader, remote_writer = await asyncio.open_connection(
            urlsplit(frontend_target).hostname, urlsplit(frontend_target).port
        )

        async def pump(source, destination):
            try:
                while data := await source.read(65536):
                    destination.write(data)
                    await destination.drain()
            except (ConnectionError, OSError):
                pass
            finally:
                destination.close()

        await asyncio.gather(pump(reader, remote_writer), pump(remote_reader, writer))

    # Vite allows localhost, not the Docker service hostname. Forward bytes, not API mocks.
    frontend_target = frontend
    proxy = await asyncio.start_server(forward, "127.0.0.1", 0)
    frontend = f"http://127.0.0.1:{proxy.sockets[0].getsockname()[1]}"
    fixture_reports = {}
    fixture_documents = {}
    transfer_text = "Gr\u00fc\u00dfe \u4e16\u754c \U0001f642\n<b>literal</b>; $(not-a-command)"
    transfer_bytes = transfer_text.encode()
    slow = asyncio.Event()
    handoff_load = asyncio.Event()
    handoff_download = asyncio.Event()

    async def fixture(reader, writer):
        try:
            headers = (await reader.readuntil(b"\r\n\r\n")).decode("latin1")
            method, path, _ = headers.splitlines()[0].split()
            if method == "GET":
                evidence["fixtureRequests"].append(path)
            if method == "GET" and path == "/":
                evidence["fixtureLoads"] += 1
            if path == "/slow":
                await slow.wait()
            if path == "/handoff":
                await handoff_load.wait()
            if path == "/redirect":
                writer.write(
                    b"HTTP/1.1 302 Found\r\nLocation: /two\r\nContent-Length: 0\r\n"
                    b"Connection: close\r\n\r\n"
                )
                await writer.drain()
                return
            if method == "POST":
                length = next(
                    int(line.split(":", 1)[1])
                    for line in headers.splitlines()[1:]
                    if line.lower().startswith("content-length:")
                )
                assert length < 65536
                fixture_reports[path] = json.loads(await reader.readexactly(length))
                if path == "/geometry":
                    fixture_documents[fixture_reports[path]["documentId"]] = fixture_reports[path]
            body = (
                b"<!doctype html><title>Browser E2E local fixture</title>"
                b'<body style="margin:0;background:#f8fafc;font:28px sans-serif">'
                b'<header style="background:#16a34a;color:white;padding:48px">'
                b'REAL BACKEND RELAY</header><main style="padding:48px">'
                b"<p><button id=\"spa\" onclick=\"history.pushState({},'','/spa');"
                b"document.title='SPA fixture';geometry()\">SPA</button> "
                b'<button id="draft" onclick="setTimeout(() => {'
                b"history.pushState({},'','/spa#changed');"
                b"document.title='Changed fixture';geometry()}, 1200)\">Delayed SPA</button> "
                b"<button id=\"popup\" onclick=\"window.open('/popup','',"
                b"'width=500,height=400')\">Popup</button></p>"
                b"<h1>Isolated browser smoke</h1><p>Local fixture. No external website.</p>"
                b'<label>Clipboard check <textarea id="input" autofocus '
                b'style="display:block;font:24px sans-serif;width:90%;height:100px"></textarea>'
                b'</label><p><input id="upload" type="file"></p>'
                b'<a id="download" href="/download" download="fixture-download.txt">'
                b"Download fixture</a>"
                b'<button id="background" onclick="const a=document.createElement(\'a\');'
                b"a.href='/background-download';a.download='background.txt';a.click()\">"
                b"Background download</button>"
                b"</main><script>"
                b"const report = (path, data) => fetch(path, {method:'POST',"
                b"headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});"
                b"const input = document.querySelector('#input'), documentId = crypto.randomUUID();"
                b"let copies = 0;"
                b"const previousProfile=localStorage.getItem('profile-sentinel');"
                b"localStorage.setItem('profile-sentinel', 'persistent-browser-e2e');"
                b"const geometry = () => report('/geometry', {documentId, value:input.value,"
                b"profile:localStorage.getItem('profile-sentinel'),"
                b"previousProfile,"
                b"screen:{width:screen.width,height:screen.height},"
                b"outer:{width:outerWidth,height:outerHeight},"
                b"inner:{width:innerWidth,height:innerHeight},copies,url:location.href,"
                b"controls:Object.fromEntries(['spa','draft','popup','background'].map(id => "
                b"[id,document.getElementById(id).getBoundingClientRect().toJSON()]))});"
                b"if(location.pathname!=='/') document.title='Fixture '+location.pathname;"
                b"window.onresize = window.onfocus = geometry; geometry();"
                b"document.addEventListener('visibilitychange', geometry);"
                b"input.onselect = () => report('/selection', {value:input.value,"
                b"start:input.selectionStart,end:input.selectionEnd});"
                b"input.oncopy = e => {report('/copy', {trusted:e.isTrusted,count:++copies,"
                b"text:input.value.slice(input.selectionStart,input.selectionEnd)}); geometry();};"
                b"document.addEventListener('focusin', e => report('/focus', e.target.id));"
                b"document.querySelector('#upload').onclick = () => report('/file-click', true);"
                b"input.oninput = e => {report('/input', e.target.value); geometry();};"
                b"document.querySelector('#upload').onchange = async e => {"
                b"const f = e.target.files[0];"
                b"await report('/upload', {name:f.name,text:await f.text()});};"
                b"</script></body>"
            )
            extra_headers = b"Content-Type: text/html; charset=utf-8\r\n"
            if path in {"/download", "/background-download"}:
                body = transfer_bytes
                extra_headers = (
                    b"Content-Type: application/octet-stream\r\n"
                    b'Content-Disposition: attachment; filename="fixture-download.txt"\r\n'
                )
            if path == "/background-download":
                writer.write(
                    b"HTTP/1.1 200 OK\r\nConnection: close\r\n"
                    b"Content-Type: application/octet-stream\r\n"
                    b'Content-Disposition: attachment; filename="background.txt"\r\n'
                    + f"Content-Length: {len(body)}\r\n\r\n".encode()
                    + body[:1]
                )
                await writer.drain()
                await handoff_download.wait()
                writer.write(body[1:])
                await writer.drain()
                evidence["backgroundDownloadSent"] = True
                return
            writer.write(
                b"HTTP/1.1 200 OK\r\nConnection: close\r\n"
                + extra_headers
                + f"Content-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass  # Chromium may close speculative HTTP connections without sending a request.
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    server = await asyncio.start_server(fixture, "127.0.0.1", 0)
    fixture_url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/"
    agent = task = browser = page = None
    with tempfile.TemporaryDirectory(prefix="browser-e2e-profile-") as temporary:
        home = Path(temporary)
        playwright = await async_playwright().start()
        try:
            async with asyncio.timeout(320):
                browser = await playwright.chromium.launch(
                    channel="chromium",
                    headless=True,
                    ignore_default_args=["--hide-scrollbars"],
                    chromium_sandbox=True,
                    env={
                        "HOME": str(home),
                        "PATH": "/usr/local/bin:/usr/bin:/bin",
                        "LANG": "C.UTF-8",
                        "PLAYWRIGHT_BROWSERS_PATH": "/usr/local/lib/matoloop-agent/tools/browser",
                    },
                )
                context = await browser.new_context(viewport={"width": 1440, "height": 1000})
                context.set_default_timeout(30000)
                await context.grant_permissions(
                    ["clipboard-read", "clipboard-write"], origin=frontend
                )
                # Observe real native APIs/events without replacing their data or implementation.
                await context.add_init_script("""
                    window.browserE2E = {pastes: [], items: []};
                    globalThis.WebSocket = new Proxy(WebSocket, {
                        construct(target, args, receiver) {
                            const socket = Reflect.construct(target, args, receiver);
                            if (String(args[0]).includes('/api/browsers/') &&
                                !String(args[0]).includes('/view?'))
                                window.browserE2EControl = socket;
                            return socket;
                        }
                    });
                    document.addEventListener('paste', event => browserE2E.pastes.push({
                        trusted: event.isTrusted, text: event.clipboardData?.getData('text/plain'),
                        viewer: Boolean(event.target.closest('.browser-app__view'))
                    }), true);
                    if (globalThis.ClipboardItem)
                    globalThis.ClipboardItem = new Proxy(ClipboardItem, {
                        construct(target, args, receiver) {
                            browserE2E.items.push({
                                promise: args[0]['text/plain'] instanceof Promise
                            });
                            return Reflect.construct(target, args, receiver);
                        }
                    });
                """)

                async def api(method, path, data=None):
                    response = await context.request.fetch(
                        frontend + "/api" + path,
                        method=method,
                        data=data,
                        timeout=90000,
                    )
                    assert response.ok, (
                        f"{method} {path}: {response.status} {await response.text()}"
                    )
                    return await response.json() if response.status != 204 else None

                await api(
                    "POST",
                    "/auth/login",
                    {
                        "username": seed["username"],
                        "password": seed["password"],
                    },
                )
                pairing = await api("POST", "/execution-agents", {"alias": seed["username"]})
                agent_id = pairing["agent"]["id"]
                evidence["agentId"] = agent_id
                key = secrets.token_urlsafe(48)
                response = await context.request.post(
                    frontend + "/api/execution-agents/pair",
                    headers={"Authorization": "Bearer " + pairing["pairingToken"]},
                    data={
                        "agentId": agent_id,
                        "accessKeyHash": hashlib.sha256(key.encode()).hexdigest(),
                    },
                )
                assert response.status == 204, "Pairing failed"
                config_path = home / "config.json"
                config_path.write_text(
                    json.dumps(
                        {
                            "backendUrl": backend + "/api/execution-agents/connect",
                            "agentId": agent_id,
                            "allowInsecure": True,
                            "homeDirectory": str(home),
                        }
                    )
                )
                agent = MatoLoopAgent(AgentConfig.load(config_path), key)
                dispatch = agent.browser.dispatch

                async def observe_dispatch(operation, arguments):
                    record = {"operation": operation}
                    if operation.endswith(".control"):
                        record["manual"] = arguments.manual
                    evidence["agentOperations"].append(record)
                    try:
                        result = await dispatch(operation, arguments)
                        record["completed"] = True
                        return result
                    except BaseException as error:
                        record["error"] = type(error).__name__
                        record["code"] = getattr(error, "code", None)
                        raise

                agent.browser.dispatch = observe_dispatch
                task = asyncio.create_task(agent.run_forever())
                async with asyncio.timeout(30):
                    while not (await api("GET", f"/execution-agents/{agent_id}"))["connected"]:
                        await asyncio.sleep(0.2)
                integration = await api(
                    "POST",
                    "/integrations/browser",
                    {
                        "name": "Isolierter Testbrowser",
                        "executionAgentId": agent_id,
                        "enabled": True,
                        "config": {},
                    },
                )
                integration_id = integration["instances"][0]["id"]
                evidence["integrationId"] = integration_id
                for identifier in (seed["sessionId"], seed["secondSessionId"]):
                    await api(
                        "PATCH",
                        f"/sessions/{identifier}",
                        {"preferred_execution_agent_id": agent_id},
                    )
                status = await api("GET", f"/integrations/browser/{integration_id}/status")
                evidence["status"] = status
                assert status["state"] == "ready", status
                print(f"READY: {status}", flush=True)

                page = await context.new_page()
                page.on("pageerror", lambda error: evidence["errors"].append(str(error)))
                page.on(
                    "request",
                    lambda request: (
                        evidence["clipboardRequests"].append(
                            {
                                "method": request.method,
                                "url": request.url,
                            }
                        )
                        if f"/browsers/{integration_id}/clipboard" in request.url
                        else None
                    ),
                )

                def websocket(socket, viewer="A"):
                    if "/api/browsers/" not in socket.url:
                        return
                    record = {
                        "url": socket.url,
                        "viewer": viewer,
                        "binaryBytes": 0,
                        "messages": [],
                        "sent": [],
                    }
                    evidence["websockets"].append(record)

                    def frame(payload):
                        if isinstance(payload, bytes):
                            record["binaryBytes"] += len(payload)
                        else:
                            record["messages"].append(json.loads(payload))

                    socket.on("framereceived", frame)
                    socket.on(
                        "framesent",
                        lambda payload: (
                            record["sent"].append(json.loads(payload))
                            if isinstance(payload, str)
                            else None
                        ),
                    )
                    socket.on("close", lambda: record.update(closed=True))
                    socket.on("socketerror", lambda error: record.update(error=str(error)))

                page.on("websocket", websocket)
                if scenario is not None:
                    await scenario(
                        page,
                        api,
                        agent,
                        seed,
                        frontend,
                        fixture_url,
                        artifacts,
                        evidence,
                        fixture_reports,
                    )
                    await api("DELETE", f"/integrations/browser/{integration_id}")
                    await api("DELETE", f"/execution-agents/{agent_id}")
                    evidence["result"] = "PASS"
                    return
                await page.goto(frontend + f"/?session={seed['sessionId']}&controlCenter=browser")
                await expect(page.get_by_text("Manuelle Steuerung", exact=True)).to_be_visible()
                await expect(
                    page.get_by_role("button", name="Browser öffnen", exact=True)
                ).to_have_count(0)
                assert await page.evaluate(
                    "isSecureContext && !!navigator.clipboard && !!ClipboardItem"
                )
                canvas = page.locator(".browser-app canvas")
                await expect(canvas).to_be_visible()
                await expect(canvas).to_have_attribute("tabindex", "0")
                await expect(canvas).to_have_attribute(
                    "aria-label", re.compile("Entfernter Browser")
                )
                control = next(
                    item for item in evidence["websockets"] if "/view?" not in item["url"]
                )
                state = next(item for item in control["messages"] if item["type"] == "state")
                browser_id, view_id = state["browserId"], state["viewId"]
                query = f"?browserId={browser_id}&sessionId={seed['sessionId']}&viewId={view_id}"
                address = page.get_by_role("textbox", name="Webadresse", exact=True)
                web_tabs = page.locator('[aria-label="Webseiten"]')
                more = page.get_by_role("button", name="Weitere Browseraktionen", exact=True)
                menu = page.locator(".q-menu.app-action-menu")
                expected_errors = []

                async def navigation(predicate, name):
                    state = None
                    try:
                        async with asyncio.timeout(15):
                            while True:
                                state = next(
                                    (
                                        m
                                        for m in reversed(control["messages"])
                                        if m["type"] == "navigation"
                                    ),
                                    None,
                                )
                                if state and predicate(state):
                                    evidence.setdefault("navigation", []).append(
                                        {"name": name, **state}
                                    )
                                    return state
                                errors = [
                                    m
                                    for m in control["messages"]
                                    if m["type"] == "error" and m not in expected_errors
                                ]
                                assert not errors, errors
                                await asyncio.sleep(0.05)
                    except TimeoutError:
                        raise AssertionError(
                            f"{name}: last navigation={state}; sent={control['sent'][-5:]}"
                        ) from None

                async def navigate_url(url):
                    await expect(
                        web_tabs.get_by_role("button", name="Neuer Tab", exact=True)
                    ).to_be_enabled()
                    await canvas.focus()
                    await page.keyboard.press("Control+l")
                    await expect(address).to_be_focused()
                    await address.fill(url)
                    await address.press("Enter")
                    return await navigation(
                        lambda s: (
                            not s["loading"]
                            and any(
                                t["id"] == s["activeTabId"] and t["url"] == url for t in s["tabs"]
                            )
                        ),
                        url,
                    )

                async def menu_action(label):
                    await more.click()
                    # QItem's accessible name also includes its decorative Material icon.
                    item = menu.get_by_text(label, exact=True).locator("..")
                    await expect(item).to_be_visible()
                    await item.focus()
                    await page.keyboard.press("Enter")
                    await expect(menu).not_to_be_visible()

                async def overlay(name, rgb, height):
                    # noVNC canvas contains X desktop pixels, including native browser overlays.
                    mismatch = await canvas.evaluate(
                        """(c, {rgb, height}) => {
                        // Exclude the fixture's native vertical scrollbar, not browser chrome.
                        const p = c.getContext('2d').getImageData(0, 0, c.width - 20, height).data;
                        let different = 0;
                        for (let i = 0; i < p.length; i += 4)
                            if (p[i] !== rgb[0] || p[i+1] !== rgb[1] || p[i+2] !== rgb[2])
                                different++;
                        return different;
                    }""",
                        {"rgb": rgb, "height": height},
                    )
                    await canvas.screenshot(path=str(artifacts / f"{name}.png"))
                    evidence.setdefault("overlays", []).append({"name": name, "pixels": mismatch})
                    assert mismatch < 100, f"{name}: {mismatch} native overlay pixels"

                initial = await navigation(
                    lambda s: s["completed"] is True and s["activeTabId"] is not None,
                    "initial RFB state subscription",
                )
                assert [m for m in control["sent"] if m["type"] == "navigation"] == [
                    {"type": "navigation", "action": "state"}
                ]
                first_tab = initial["activeTabId"]
                assert len(initial["tabs"]) == 1 and not initial["nativeChrome"], initial
                await expect(address).to_have_value("about:blank")
                # RFBconnect precedes the first framebuffer update; inspect the first painted frame.
                await page.wait_for_function("""() => {
                    const c = document.querySelector('.browser-app canvas');
                    return c.getContext('2d').getImageData(10, 200, 1, 1).data[0] === 255;
                }""")
                await overlay("immediate-startup", [255, 255, 255], 200)
                await navigate_url(fixture_url + "one")
                await expect(
                    web_tabs.get_by_role("tab", name="Fixture /one", exact=True)
                ).to_have_attribute("aria-selected", "true")
                await page.wait_for_function("""() => {
                    const c = document.querySelector('.browser-app canvas');
                    return c.getContext('2d').getImageData(10, 10, 1, 1).data[1] === 163;
                }""")
                await overlay("first-navigation", [22, 163, 74], 40)
                await canvas.hover(position={"x": 20, "y": 250})
                await asyncio.sleep(
                    4
                )  # Let entry toasts expire before probing the top-edge trigger.
                box = await canvas.bounding_box()
                await canvas.hover(position={"x": box["width"] / 2, "y": 0})
                await asyncio.sleep(0.6)
                await overlay("top-edge", [22, 163, 74], 40)

                await navigate_url(fixture_url + "two")
                await page.get_by_role("button", name="Zurück", exact=True).click()
                await expect(address).to_have_value(fixture_url + "one")
                await page.get_by_role("button", name="Vorwärts", exact=True).click()
                await expect(address).to_have_value(fixture_url + "two")
                before = evidence["fixtureRequests"].count("/two")
                await page.get_by_role("button", name="Neu laden", exact=True).click()
                await navigation(
                    lambda s: (
                        not s["loading"] and evidence["fixtureRequests"].count("/two") > before
                    ),
                    "reload",
                )

                await web_tabs.get_by_role("button", name="Neuer Tab", exact=True).click()
                second = await navigation(
                    lambda s: len(s["tabs"]) == 2 and s["activeTabId"] != first_tab, "new tab"
                )
                second_tab = second["activeTabId"]
                await navigate_url(fixture_url + "one")
                await web_tabs.get_by_role("tab", name="Fixture /two", exact=True).click()
                await navigation(lambda s: s["activeTabId"] == first_tab, "select first tab")
                await web_tabs.get_by_role(
                    "button", name="Fixture /one schließen", exact=True
                ).click()
                await navigation(lambda s: len(s["tabs"]) == 1, "close background tab")
                await canvas.focus()
                await page.keyboard.press("Control+t")
                await navigation(
                    lambda s: (
                        len(s["tabs"]) == 2 and s["activeTabId"] not in {first_tab, second_tab}
                    ),
                    "Ctrl+T",
                )
                await expect(
                    web_tabs.get_by_role("button", name="Neuer Tab", exact=True)
                ).to_be_enabled()
                await canvas.focus()
                await page.keyboard.press("Control+w")
                await navigation(
                    lambda s: len(s["tabs"]) == 1 and s["activeTabId"] == first_tab, "Ctrl+W"
                )

                async def fixture_click(name):
                    rect = fixture_reports["/geometry"]["controls"][name]
                    await canvas.click(
                        position={
                            "x": rect["x"] + rect["width"] / 2,
                            "y": rect["y"] + rect["height"] / 2,
                        }
                    )

                await expect(
                    web_tabs.get_by_role("button", name="Neuer Tab", exact=True)
                ).to_be_enabled()
                await address.click()
                await page.keyboard.press("Control+a")
                await page.keyboard.type(fixture_url + "discarded-draft")
                await expect(address).to_be_focused()
                await expect(address).to_have_value(fixture_url + "discarded-draft")
                await fixture_click("spa")
                await expect(canvas).to_be_focused()
                await expect(
                    web_tabs.get_by_role("tab", name="SPA fixture", exact=True)
                ).to_be_visible()
                await navigation(
                    lambda s: s["completed"] is False and s["tabs"][0]["title"] == "SPA fixture",
                    "unsolicited SPA",
                )
                await expect(address).to_have_value(fixture_url + "spa")
                evidence["pointerAddressSync"] = {
                    "discardedDraft": fixture_url + "discarded-draft",
                    "addressAfterCanvasClick": await address.input_value(),
                    "focused": await page.evaluate("document.activeElement.tagName"),
                }
                await page.screenshot(path=str(artifacts / "pointer-spa-address.png"))
                await fixture_click("draft")
                await address.click()
                await address.fill(fixture_url + "unsent-draft")
                await expect(address).to_be_focused()
                await navigation(
                    lambda s: s["completed"] is False and s["tabs"][0]["url"].endswith("#changed"),
                    "unsolicited update during address edit",
                )
                await expect(address).to_have_value(fixture_url + "unsent-draft")
                await address.press("Escape")
                await expect(address).to_have_value(fixture_url + "spa#changed")
                await fixture_click("popup")
                popup = await navigation(
                    lambda s: len(s["tabs"]) == 2 and s["activeTabId"] != first_tab,
                    "unsolicited popup",
                )
                assert popup["completed"] is False, popup
                await expect(address).to_have_value(fixture_url + "popup")
                await web_tabs.get_by_role(
                    "button", name="Fixture /popup schließen", exact=True
                ).click()
                await navigation(lambda s: len(s["tabs"]) == 1, "close popup")
                await canvas.focus()
                await page.keyboard.press("Control+l")
                await address.fill(fixture_url + "redirect")
                await address.press("Enter")
                await expect(address).to_have_value(fixture_url + "two")
                assert "/redirect" in evidence["fixtureRequests"]

                await more.click()
                chrome = menu.get_by_role("switch", name="Browseroberfläche anzeigen", exact=True)
                await chrome.click()
                await navigation(lambda s: s["nativeChrome"] and s["completed"], "native chrome on")
                await page.keyboard.press("Escape")
                async with asyncio.timeout(10):
                    while (
                        fixture_reports["/geometry"]["inner"]["height"]
                        >= fixture_reports["/geometry"]["screen"]["height"]
                    ):
                        await asyncio.sleep(0.1)
                await page.screenshot(path=str(artifacts / "native-chrome.png"))
                await canvas.click(position={"x": 200, "y": 62})
                await expect(canvas).to_be_focused()
                await page.keyboard.press("Control+a")
                await page.keyboard.type("chrome://settings", delay=10)
                await page.keyboard.press("Enter")
                await expect(address).to_have_value(re.compile(r"^chrome://settings"))
                await page.screenshot(path=str(artifacts / "native-settings.png"))
                await navigate_url(fixture_url + "one")
                await canvas.click(position={"x": 20, "y": 200})
                normal_inner = fixture_reports["/geometry"]["inner"].copy()
                await page.keyboard.press("F12")
                async with asyncio.timeout(10):
                    while fixture_reports["/geometry"]["inner"] == normal_inner:
                        await asyncio.sleep(0.1)
                await page.screenshot(path=str(artifacts / "scoped-f12-devtools.png"))
                await page.keyboard.press("F12")
                async with asyncio.timeout(10):
                    while fixture_reports["/geometry"]["inner"] != normal_inner:
                        await asyncio.sleep(0.1)
                await more.click()
                await chrome.click()
                await navigation(
                    lambda s: not s["nativeChrome"] and s["completed"], "native chrome off"
                )
                await page.keyboard.press("Escape")
                await canvas.focus()
                await page.keyboard.press("Control+l")
                await address.fill(fixture_url + "slow")
                await address.press("Enter")
                await page.get_by_role("button", name="Laden stoppen", exact=True).click()
                await navigation(lambda s: not s["loading"] and s["completed"], "stop loading")
                slow.set()
                await navigate_url(fixture_url)
                await expect(
                    page.get_by_role("button", name="Neu laden", exact=True)
                ).to_be_enabled()
                print(
                    "NAVIGATION PASS: initial state, tabs, history, SPA/draft, popup, redirect, "
                    "native chrome/settings, F12, stop",
                    flush=True,
                )
                tabs = await api("GET", f"/browsers/{integration_id}/downloads{query}")
                assert any(tab["url"] == fixture_url for tab in tabs["tabs"]), tabs
                evidence["tabs"] = tabs
                evidence["autostart"] = "persisted active tab opened without a launch click"
                worker_pid = agent.browser.worker.pid
                initial_view = next(
                    item for item in evidence["websockets"] if "/view?" in item["url"]
                )
                live_views = [initial_view]
                dialog = page.get_by_role("dialog").filter(has=page.locator(".integration-dialog"))
                await canvas.focus()
                await page.evaluate("text => navigator.clipboard.writeText(text)", transfer_text)
                assert await page.evaluate("navigator.clipboard.readText()") == transfer_text
                async with page.expect_response(
                    lambda response: (
                        "/clipboard?" in response.url and response.request.method == "PUT"
                    )
                ) as clipboard_response:
                    await page.keyboard.press("Control+v")
                response = await clipboard_response.value
                assert response.ok, await response.text()
                assert response.request.post_data_json == {"text": transfer_text}
                assert (await response.json())["text"] == transfer_text
                async with asyncio.timeout(15):
                    while fixture_reports.get("/input") != transfer_text:
                        await asyncio.sleep(0.1)
                assert await page.evaluate("browserE2E.pastes") == [
                    {"trusted": True, "text": transfer_text, "viewer": True}
                ]
                await expect(dialog).not_to_be_visible()
                document_id = fixture_reports["/geometry"]["documentId"]
                remote = fixture_reports["/geometry"]
                evidence["postChromeGeometry"] = remote
                assert remote["inner"] == remote["screen"], remote

                geometry_script = """() => {
                    const host = document.querySelector('.browser-app__view');
                    const canvas = host?.querySelector('canvas');
                    if (!canvas) return null;
                    const h = host.getBoundingClientRect(), c = canvas.getBoundingClientRect();
                    return {host: h.toJSON(), canvas: c.toJSON(),
                        pixels: {width: canvas.width, height: canvas.height},
                        target: {width: Math.floor(h.width), height: Math.floor(h.height)}};
                }"""

                async def render(name, width, height, theme, *, manual=True):
                    await page.set_viewport_size({"width": width, "height": height})
                    await page.emulate_media(color_scheme=theme)
                    await expect(page.locator("body")).to_have_class(
                        re.compile(rf".*body--{theme}.*")
                    )
                    await expect(canvas).to_be_visible()
                    # Real desktop pixels must equal the CSS content box, not a scaled preset.
                    async with asyncio.timeout(30):
                        while True:
                            measured = await page.evaluate(geometry_script)
                            remote = fixture_reports.get("/geometry", {})
                            evidence["lastGeometry"] = {
                                "name": name,
                                "local": measured,
                                "remote": remote,
                            }
                            if measured and (
                                measured["pixels"] == measured["target"] == remote.get("screen")
                                and measured["canvas"]["x"] >= 0
                                and measured["canvas"]["right"] <= width + 1
                                and measured["canvas"]["bottom"] <= height + 1
                                and remote.get("inner", {}).get("width")
                                == measured["target"]["width"]
                                and remote.get("inner", {}).get("height")
                                == measured["target"]["height"]
                                and all(
                                    abs(measured["host"][key] - measured["canvas"][key]) < 1
                                    for key in ("x", "y", "width", "height")
                                )
                                and any(
                                    message["type"] == "resized"
                                    and message["width"] == measured["target"]["width"]
                                    and message["height"] == measured["target"]["height"]
                                    and message["browserId"] == browser_id
                                    for message in control["messages"]
                                )
                            ):
                                break
                            await asyncio.sleep(0.1)
                    assert (
                        remote["value"] == transfer_text and remote["documentId"] == document_id
                    ), remote
                    assert evidence["fixtureLoads"] == 1, evidence["fixtureLoads"]
                    assert (
                        agent.browser.browser_id == browser_id
                        and agent.browser.worker.pid == worker_pid
                    )
                    assert evidence["websockets"] == [control, *live_views]
                    assert not control.get("closed") and not live_views[-1].get("closed")
                    assert all(
                        message["browserId"] == browser_id
                        for message in control["messages"]
                        if message["type"] == "state"
                    )
                    bounds = measured["canvas"]
                    assert bounds["x"] >= 0 and bounds["right"] <= width + 1, measured
                    assert bounds["y"] >= 0 and bounds["bottom"] <= height + 1, measured
                    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    toolbar = page.locator('[aria-label="Browsersteuerung"]')
                    toolbar_box = await toolbar.bounding_box()
                    tabs_box = await web_tabs.bounding_box()
                    address_box = await address.bounding_box()
                    assert tabs_box["y"] + tabs_box["height"] <= toolbar_box["y"] + 1
                    assert toolbar_box["y"] + toolbar_box["height"] <= bounds["y"] + 1
                    assert address_box["width"] >= 80, address_box
                    buttons = []
                    file_labels = (
                        "Text kopieren",
                        "Text einfügen",
                        "Datei hochladen",
                        "Downloads",
                    )
                    labels = ("Zurück", "Vorwärts", "Neu laden", "Weitere Browseraktionen")
                    if width >= 600:
                        labels += file_labels
                    else:
                        for label in file_labels:
                            await expect(
                                toolbar.get_by_role("button", name=label, exact=True)
                            ).to_have_count(0)
                    for label in labels:
                        button = toolbar.get_by_role("button", name=label, exact=True)
                        await expect(button).to_be_visible()
                        if manual and label not in {"Zurück", "Vorwärts"}:
                            await expect(button).to_be_enabled()
                        box = await button.bounding_box()
                        assert (
                            box["width"] <= toolbar_box["height"]
                            and box["height"] <= toolbar_box["height"]
                        ), box
                        assert (
                            box["x"] >= toolbar_box["x"]
                            and box["x"] + box["width"] <= toolbar_box["x"] + toolbar_box["width"]
                        ), box
                        buttons.append(box)
                    assert len({box["y"] for box in buttons}) == 1, buttons
                    await page.screenshot(
                        path=str(artifacts / f"{name}.png"), full_page=True, animations="disabled"
                    )
                    evidence["cases"].append(
                        {
                            "name": name,
                            **measured,
                            "remote": remote,
                            "toolbar": toolbar_box,
                            "tabBar": tabs_box,
                            "address": address_box,
                        }
                    )
                    print(f"RENDER PASS: {name} {measured}", flush=True)

                for size, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                    for theme in ("light", "dark"):
                        await render(f"{size}-{theme}", width, height, theme)
                        await canvas.focus()
                        await page.keyboard.press("Control+Alt+Escape")
                        await expect(more).to_be_focused()
                        await page.keyboard.press("Enter")
                        for label in (
                            "Browseroberfläche anzeigen",
                            "Status prüfen",
                            "Auflösung an Fenster anpassen",
                            "Skalierung ausschalten (1:1)",
                            "Einrichtung",
                            "Browser schließen",
                        ):
                            await expect(menu.get_by_text(label, exact=True)).to_be_visible()
                        if width < 600:
                            for label in (
                                "Text kopieren",
                                "Text einfügen",
                                "Datei hochladen",
                                "Downloads",
                            ):
                                await expect(menu.get_by_text(label, exact=True)).to_be_visible()
                        await expect(menu).to_have_css("opacity", "1")
                        await expect(menu).to_have_css("transform", "none")
                        menu_box = await menu.bounding_box()
                        assert menu_box["x"] >= 0 and menu_box["x"] + menu_box["width"] <= width, (
                            menu_box
                        )
                        assert (
                            menu_box["y"] >= 0 and menu_box["y"] + menu_box["height"] <= height
                        ), menu_box
                        await page.screenshot(
                            path=str(artifacts / f"{size}-{theme}-menu.png"),
                            full_page=True,
                            animations="disabled",
                        )
                        await page.keyboard.press("Escape")
                        await expect(menu).not_to_be_visible()
                await render("desktop-dark-return", 1440, 1000, "dark")
                await render("unusual-dark", 1173, 913, "dark")

                # Focus outside the viewer must never forward native clipboard shortcuts.
                blocked_text = "Local only: do not paste into the remote browser"
                requests_before = len(evidence["clipboardRequests"])
                await more.focus()
                await page.evaluate("text => navigator.clipboard.writeText(text)", blocked_text)
                await page.keyboard.press("Control+v")
                await page.keyboard.press("Control+c")
                await render("focus-guard-dark", 1175, 915, "dark")
                assert len(evidence["clipboardRequests"]) == requests_before
                assert await page.evaluate("navigator.clipboard.readText()") == blocked_text
                assert "/copy" not in fixture_reports

                # A different remote clipboard rules out GET of the previously pasted text.
                await api(
                    "PUT",
                    f"/browsers/{integration_id}/clipboard{query}",
                    {"text": "Remote stale sentinel"},
                )
                await canvas.focus()
                await page.keyboard.press("Control+a")
                async with asyncio.timeout(15):
                    while fixture_reports.get("/selection") != {
                        "value": transfer_text,
                        "start": 0,
                        "end": len(transfer_text.encode("utf-16-le")) // 2,
                    }:
                        await asyncio.sleep(0.1)
                evidence["clipboard"] = []
                for count in (1, 2):
                    sentinel = f"Local clipboard changed before copy {count}"
                    await page.evaluate("text => navigator.clipboard.writeText(text)", sentinel)
                    assert await page.evaluate("navigator.clipboard.readText()") == sentinel
                    async with page.expect_response(
                        lambda response: (
                            "/clipboard/copy?" in response.url and response.request.method == "POST"
                        )
                    ) as clipboard_response:
                        await page.keyboard.press("Control+c")
                    response = await clipboard_response.value
                    assert response.ok, await response.text()
                    assert (await response.json())["text"] == transfer_text
                    await page.wait_for_function(
                        "async text => await navigator.clipboard.readText() === text",
                        arg=transfer_text,
                    )
                    async with asyncio.timeout(15):
                        while fixture_reports.get("/copy", {}).get("count") != count:
                            await asyncio.sleep(0.1)
                    assert fixture_reports["/copy"] == {
                        "trusted": True,
                        "count": count,
                        "text": transfer_text,
                    }
                    assert await page.evaluate("browserE2E.items") == [{"promise": True}] * count
                    await expect(dialog).not_to_be_visible()
                    evidence["clipboard"].append(
                        {
                            "copy": count,
                            "nativeSelection": fixture_reports["/copy"],
                            "localClipboard": await page.evaluate("navigator.clipboard.readText()"),
                            "promiseClipboardItem": True,
                        }
                    )
                assert [item["method"] for item in evidence["clipboardRequests"]] == [
                    "PUT",
                    "POST",
                    "POST",
                ]
                await page.screenshot(
                    path=str(artifacts / "native-clipboard-copy.png"),
                    full_page=True,
                    animations="disabled",
                )
                print(
                    "CLIPBOARD PASS: trusted UTF-8 paste and repeated confirmed native copy "
                    "to local clipboard",
                    flush=True,
                )
                await render("mobile-dark-clipboard", 390, 844, "dark")

                # The remote textarea retains focus through native clipboard transfers.
                await canvas.focus()
                await page.keyboard.press("Tab")
                async with asyncio.timeout(10):
                    while fixture_reports.get("/focus") != "upload":
                        await asyncio.sleep(0.1)
                await page.keyboard.press("Space")
                async with asyncio.timeout(10):
                    while not fixture_reports.get("/file-click"):
                        await asyncio.sleep(0.1)
                await menu_action("Datei hochladen")
                await dialog.locator('input[type="file"]').set_input_files(
                    {
                        "name": "fixture-upload.txt",
                        "mimeType": "text/plain",
                        "buffer": transfer_bytes,
                    }
                )
                await expect(
                    dialog.get_by_role("button", name="Datei bereitstellen")
                ).to_be_enabled()
                await expect(dialog.locator(".integration-dialog").locator("..")).to_have_css(
                    "transform", "none"
                )
                await page.screenshot(
                    path=str(artifacts / "mobile-dark-upload.png"),
                    full_page=True,
                    animations="disabled",
                )
                async with page.expect_response(
                    lambda response: (
                        "/upload?" in response.url and response.request.method == "POST"
                    )
                ) as upload_response:
                    await dialog.get_by_role("button", name="Datei bereitstellen").click()
                response = await upload_response.value
                assert response.ok, await response.text()
                assert (await response.json())["chosen"], "Remote file chooser was not populated"
                await expect(dialog).not_to_be_visible()
                async with asyncio.timeout(15):
                    while "/upload" not in fixture_reports:
                        await asyncio.sleep(0.1)
                assert fixture_reports["/upload"] == {
                    "name": "fixture-upload.txt",
                    "text": transfer_text,
                }
                evidence["upload"] = "UI multipart upload reached remote fixture byte-for-byte"
                print("UPLOAD PASS: remote fixture received exact file contents", flush=True)

                await canvas.focus()
                await page.keyboard.press("Tab")
                async with asyncio.timeout(10):
                    while fixture_reports.get("/focus") != "download":
                        await asyncio.sleep(0.1)
                await page.keyboard.press("Enter")
                async with asyncio.timeout(15):
                    while not (await api("GET", f"/browsers/{integration_id}/downloads{query}"))[
                        "downloads"
                    ]:
                        await asyncio.sleep(0.2)
                await menu_action("Downloads")
                await expect(dialog.get_by_text("fixture-download.txt", exact=True)).to_be_visible()
                await expect(dialog.locator(".integration-dialog").locator("..")).to_have_css(
                    "transform", "none"
                )
                await page.screenshot(
                    path=str(artifacts / "mobile-dark-download.png"),
                    full_page=True,
                    animations="disabled",
                )
                async with page.expect_download() as download_event:
                    await dialog.get_by_role(
                        "button", name="fixture-download.txt herunterladen"
                    ).click()
                download = await download_event.value
                assert download.suggested_filename == "fixture-download.txt"
                assert await download.failure() is None
                assert Path(await download.path()).read_bytes() == transfer_bytes
                await dialog.get_by_role("button", name="Schließen", exact=True).click()
                await expect(dialog).not_to_be_visible()
                evidence["download"] = "local fixture download returned through UI byte-for-byte"
                print("DOWNLOAD PASS: exact file contents through backend relay", flush=True)
                await render("desktop-dark-before-attach", 1173, 913, "dark")
                requests_before = len(evidence["clipboardRequests"])
                await page.evaluate("text => navigator.clipboard.writeText(text)", blocked_text)
                await page.get_by_role("switch", name="Agent verbinden").click()
                await expect(
                    page.get_by_text(
                        "Agent verbunden. Manuelle Eingaben sind gesperrt.", exact=True
                    )
                ).to_be_visible()
                await expect(
                    page.get_by_role("button", name="Text einfügen", exact=True)
                ).to_be_disabled()
                await expect(
                    page.get_by_role("button", name="Text kopieren", exact=True)
                ).to_be_disabled()
                await expect(address).to_have_attribute("readonly", "")
                for label in ("Zurück", "Vorwärts", "Neu laden", "Neuer Tab"):
                    await expect(
                        page.get_by_role("button", name=label, exact=True)
                    ).to_be_disabled()
                await more.click()
                await expect(chrome).to_have_attribute("aria-disabled", "true")
                await page.keyboard.press("Escape")
                await canvas.focus()
                await page.keyboard.press("Control+v")
                await page.keyboard.press("Control+c")
                await page.keyboard.type("BLOCKED")
                await render("attached-dark", 1173, 913, "dark", manual=False)
                assert len(evidence["clipboardRequests"]) == requests_before
                assert await page.evaluate("navigator.clipboard.readText()") == blocked_text
                assert fixture_reports["/copy"]["count"] == 2
                # Bypass disabled controls, not the backend: the authenticated WS must deny.
                await page.evaluate("""() => window.browserE2EControl.send(JSON.stringify({
                    type:'navigation', action:'new'
                }))""")
                async with asyncio.timeout(10):
                    while not any(
                        m["type"] == "error" and m.get("operation") == "navigation"
                        for m in control["messages"]
                    ):
                        await asyncio.sleep(0.1)
                denied = next(m for m in reversed(control["messages"]) if m["type"] == "error")
                assert denied["error"]["code"] == "browser.busy", denied
                evidence["attachedNavigationDenied"] = denied
                expected_errors.append(denied)
                # The backend also rejects transfers while attached, independently of UI guards.
                for method, suffix, data in (
                    ("PUT", "", {"text": blocked_text}),
                    ("POST", "/copy", None),
                ):
                    response = await context.request.fetch(
                        frontend + f"/api/browsers/{integration_id}/clipboard{suffix}{query}",
                        method=method,
                        data=data,
                    )
                    assert (
                        response.status == 409
                        and (await response.json())["error"]["code"] == "browser.busy"
                    )
                await page.get_by_role("button", name="Manuell übernehmen", exact=True).click()
                await expect(page.get_by_text("Manuelle Steuerung", exact=True)).to_be_visible()
                await expect(
                    page.get_by_role("button", name="Text einfügen", exact=True)
                ).to_be_enabled()
                assert any(message["type"] == "view.reset" for message in control["messages"])
                views = [item for item in evidence["websockets"] if "/view?" in item["url"]]
                assert len(views) == 2 and views[0].get("closed")
                assert all(f"browserId={browser_id}" in item["url"] for item in views)
                live_views = views
                assert (
                    agent.browser.browser_id == browser_id
                    and agent.browser.worker.pid == worker_pid
                )
                assert (await api("GET", f"/browsers/{integration_id}/downloads{query}"))[
                    "tabs"
                ] == tabs["tabs"]
                evidence["attachment"] = (
                    "native focus/attachment guards, API busy guard, "
                    "takeover and new stream on same browser passed"
                )
                await render("mobile-dark-takeover", 390, 844, "dark")

                # Independent SPA viewers share a login, agent, integration and Chromium profile.
                await page.set_viewport_size({"width": 1440, "height": 1000})
                await expect(page.locator(".q-notification.bg-negative")).to_have_count(0)
                notices = """() => {
                    window.takeoverErrors = [];
                    new MutationObserver(() => {
                        for (const node of document.querySelectorAll('.q-notification.bg-negative'))
                            if (!takeoverErrors.includes(node.innerText))
                                takeoverErrors.push(node.innerText);
                    }).observe(document.body, {childList:true, subtree:true});
                }"""
                await page.evaluate(notices)
                operations_start = len(evidence["agentOperations"])
                page_b = await context.new_page()
                page_b.on("websocket", lambda socket: websocket(socket, "B"))
                page_b.on("pageerror", lambda error: evidence["errors"].append(str(error)))
                await page_b.goto(
                    frontend + f"/?session={seed['secondSessionId']}&controlCenter=browser"
                )
                await page_b.evaluate(notices)

                def latest_control(viewer):
                    return next(
                        item
                        for item in reversed(evidence["websockets"])
                        if item["viewer"] == viewer and "/view?" not in item["url"]
                    )

                async def offered(viewer, name, owner_session, expected_view):
                    button = viewer.get_by_role(
                        "button", name="Browser hier übernehmen", exact=True
                    )
                    await expect(button).to_be_enabled()
                    record = latest_control(name)
                    offer = next(
                        m for m in reversed(record["messages"]) if m["type"] == "availability"
                    )
                    assert record["messages"][0]["type"] == "availability", record
                    assert not any(m["type"] == "state" for m in record["messages"]), record
                    assert offer["status"] == "occupied" and offer["canTakeover"], offer
                    assert offer["browserId"] == browser_id and offer["viewId"] == expected_view
                    assert offer["ownerSession"]["id"] == owner_session, offer
                    await expect(viewer.locator(".browser-app canvas")).to_have_count(0)
                    await expect(
                        viewer.get_by_role("button", name="Neuer Tab", exact=True)
                    ).to_be_disabled()
                    assert not any(
                        item["operation"] == "integration.browser.open"
                        for item in evidence["agentOperations"][operations_start:]
                    )
                    return offer

                async def accepted(viewer, name, session_id):
                    await expect(
                        viewer.get_by_text("Manuelle Steuerung", exact=True)
                    ).to_be_visible()
                    await expect(viewer.locator(".browser-app canvas")).to_be_visible()
                    await expect(
                        viewer.get_by_role("button", name="Neuer Tab", exact=True)
                    ).to_be_enabled()
                    record = latest_control(name)
                    state = next(m for m in record["messages"] if m["type"] == "state")
                    assert (
                        state["browserId"] == browser_id
                        and not state["attached"]
                        and not state["busy"]
                    )
                    assert (
                        agent.browser.browser_id == browser_id
                        and agent.browser.worker.pid == worker_pid
                    )
                    assert not any(
                        item["operation"] == "integration.browser.open"
                        for item in evidence["agentOperations"][operations_start:]
                    )
                    await expect(
                        viewer.get_by_role("switch", name="Agent verbinden")
                    ).not_to_be_checked()
                    evidence.setdefault("acceptedViews", []).append(
                        {
                            "viewer": name,
                            "sessionId": session_id,
                            **state,
                            "workerPid": worker_pid,
                        }
                    )
                    return (
                        record,
                        state,
                        (
                            f"?browserId={browser_id}&sessionId={session_id}"
                            f"&viewId={state['viewId']}"
                        ),
                    )

                async def lifecycle_shot(viewer, name, width, height, theme):
                    await viewer.set_viewport_size({"width": width, "height": height})
                    await viewer.emulate_media(color_scheme=theme)
                    await expect(viewer.locator("body")).to_have_class(
                        re.compile(rf".*body--{theme}.*")
                    )
                    assert await viewer.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth"
                    )
                    banner = viewer.locator(".browser-app [role=status]")
                    await expect(banner).to_be_visible()
                    await viewer.wait_for_function("""() => {
                        const box = document.querySelector('.browser-app [role=status]')
                            ?.getBoundingClientRect();
                        return box && box.x >= 0 && box.right <= innerWidth + 1
                            && box.bottom <= innerHeight + 1;
                    }""")
                    box = await banner.bounding_box()
                    assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1, box
                    assert box["y"] + box["height"] <= height + 1, box
                    await viewer.screenshot(
                        path=str(artifacts / f"{name}.png"), animations="disabled"
                    )
                    evidence["cases"].append({"name": name, "banner": box})

                old_query = query
                offer_b = await offered(page_b, "B", seed["sessionId"], view_id)
                assert offer_b["ownerSession"]["title"] == "Browser UI smoke A", offer_b
                for size, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                    for theme in ("light", "dark"):
                        await lifecycle_shot(
                            page_b, f"occupied-{size}-{theme}", width, height, theme
                        )

                # An independent login cannot use A's acknowledged view, even for the same owner.
                other = await browser.new_context()
                login = await other.request.post(
                    frontend + "/api/auth/login",
                    data={
                        "username": seed["username"],
                        "password": seed["password"],
                    },
                )
                assert login.ok
                denied = await other.request.get(
                    frontend + f"/api/browsers/{integration_id}/downloads{query}"
                )
                assert (
                    denied.status == 503
                    and (await denied.json())["error"]["code"] == "browser.notConnected"
                )
                page_c = await other.new_page()
                page_c.on("websocket", lambda socket: websocket(socket, "C"))
                await page_c.goto(frontend + f"/?session={seed['sessionId']}&controlCenter=browser")
                await offered(page_c, "C", seed["sessionId"], view_id)
                await other.close()
                evidence["differentLogin"] = "same-owner login gets offer; A view HTTP rejected"

                # Real site traffic stays pending while the controlling UI changes.
                await page.bring_to_front()
                await expect(
                    page.get_by_role("button", name="Neuer Tab", exact=True)
                ).to_be_enabled()
                await canvas.focus()
                await page.keyboard.press("Control+End")
                await fixture_click("background")
                async with asyncio.timeout(10):
                    while "/background-download" not in evidence["fixtureRequests"]:
                        await asyncio.sleep(0.05)
                await web_tabs.get_by_role("button", name="Neuer Tab", exact=True).click()
                await navigation(lambda s: len(s["tabs"]) == 2, "handoff new tab")
                await expect(
                    page.get_by_role("button", name="Neuer Tab", exact=True)
                ).to_be_enabled()
                await address.fill(fixture_url + "handoff")
                await address.press("Enter")
                await navigation(lambda s: s["loading"], "handoff pending site load")
                async with asyncio.timeout(10):
                    while "/handoff" not in evidence["fixtureRequests"]:
                        await asyncio.sleep(0.05)
                await page_b.bring_to_front()
                # Hold the real agent queue briefly to make takingOver progress reproducible.
                async with agent.browser.lock:
                    await page_b.get_by_role(
                        "button", name="Browser hier übernehmen", exact=True
                    ).click()
                    await expect(
                        page_b.get_by_label("Browser wird hier übernommen", exact=True)
                    ).to_be_visible()
                    await lifecycle_shot(page_b, "progress-mobile-dark", 390, 844, "dark")
                    await lifecycle_shot(page_b, "progress-desktop-light", 1440, 1000, "light")
                control_b, state_b, query_b = await accepted(page_b, "B", seed["secondSessionId"])
                assert state_b["viewId"] != view_id
                assert any(m.get("status") == "takingOver" for m in control_b["messages"])
                await expect(
                    page.get_by_text(re.compile("Der Browser wurde in einer anderen Ansicht"))
                ).to_be_visible()
                await lifecycle_shot(page, "replaced-desktop-light", 1440, 1000, "light")
                await lifecycle_shot(page, "replaced-mobile-dark", 390, 844, "dark")
                assert any(m["type"] == "replaced" for m in control["messages"])
                handoff_load.set()
                handoff_download.set()
                async with asyncio.timeout(15):
                    while True:
                        transferred = await api(
                            "GET", f"/browsers/{integration_id}/downloads{query_b}"
                        )
                        if any(
                            t["url"] == fixture_url + "handoff" for t in transferred["tabs"]
                        ) and any(
                            d["filename"] == "background.txt" for d in transferred["downloads"]
                        ):
                            break
                        await asyncio.sleep(0.1)
                file = next(
                    d for d in transferred["downloads"] if d["filename"] == "background.txt"
                )
                downloaded = await context.request.get(
                    frontend + f"/api/browsers/{integration_id}/download/{file['id']}{query_b}"
                )
                assert downloaded.ok and await downloaded.body() == transfer_bytes
                assert evidence["backgroundDownloadSent"]

                for suffix in ("/downloads", "/clipboard"):
                    denied = await context.request.get(
                        frontend + f"/api/browsers/{integration_id}{suffix}{old_query}"
                    )
                    assert (
                        denied.status == 503
                        and (await denied.json())["error"]["code"] == "browser.notConnected"
                    )
                # Ordinary SPA unmount/remount and focus must not reclaim a replaced view.
                sockets_before = len(evidence["websockets"])
                router = (
                    "document.querySelector('#q-app').__vue_app__.config.globalProperties.$router"
                )
                await page.evaluate(f"() => {router}.push('/settings')")
                await page.evaluate(
                    f"path => {router}.push(path)",
                    f"/?session={seed['sessionId']}&controlCenter=browser",
                )
                await expect(
                    page.get_by_role("button", name="Browser erneut verbinden", exact=True)
                ).to_be_visible()
                await page.bring_to_front()
                await asyncio.sleep(2.2)
                await expect(canvas).to_have_count(0)
                assert len(evidence["websockets"]) == sockets_before
                await page.get_by_role(
                    "button", name="Browser erneut verbinden", exact=True
                ).click()
                await offered(page, "A", seed["secondSessionId"], state_b["viewId"])
                await page.get_by_role("button", name="Browser hier übernehmen", exact=True).click()
                control, state, query = await accepted(page, "A", seed["sessionId"])
                await page.set_viewport_size({"width": 1440, "height": 1000})
                await web_tabs.get_by_role(
                    "tab", name="Browser E2E local fixture", exact=True
                ).click()
                await navigation(lambda s: s["activeTabId"] == first_tab, "preserved form tab")
                assert fixture_documents[document_id]["value"] == transfer_text
                assert evidence["fixtureLoads"] == 1
                evidence["preservedTakeback"] = {
                    "documentId": document_id,
                    "formValue": transfer_text,
                    "siteNavigationAndDownload": "completed without cancellation",
                    "extraBrowserOpens": 0,
                }
                await page.screenshot(path=str(artifacts / "takeback-preserved-form.png"))

                await page.get_by_role("switch", name="Agent verbinden").click()
                await expect(page.get_by_role("switch", name="Agent verbinden")).to_be_checked()
                await page_b.get_by_role(
                    "button", name="Browser erneut verbinden", exact=True
                ).click()
                await offered(page_b, "B", seed["sessionId"], state["viewId"])
                await page_b.get_by_role(
                    "button", name="Browser hier übernehmen", exact=True
                ).click()
                control_b, state_b, query_b = await accepted(page_b, "B", seed["secondSessionId"])
                denied = await context.request.get(
                    frontend + f"/api/browsers/{integration_id}/downloads{query}"
                )
                assert (
                    denied.status == 503
                    and (await denied.json())["error"]["code"] == "browser.notConnected"
                )
                evidence["attachedHandoff"] = (
                    "A explicitly attached; B accepted detached; old A HTTP rejected"
                )
                evidence["attachmentRevisionCoverage"] = (
                    "internal revision requires parent backend tests; no LLM invoked"
                )

                # Same SPA session navigation releases the view, not the runtime. With no old
                # view socket, a different session still needs a deliberate takeover click.
                await page_b.evaluate(
                    f"path => {router}.push(path)",
                    f"/?session={seed['sessionId']}&controlCenter=browser",
                )
                await offered(page_b, "B", seed["secondSessionId"], state_b["viewId"])
                assert control_b.get("closed") and {"type": "release"} in control_b["sent"]
                assert {"type": "close"} not in control_b["sent"]
                await asyncio.sleep(2.2)
                assert (
                    agent.browser.browser_id == browser_id
                    and agent.browser.worker.pid == worker_pid
                )
                assert agent.browser.view_task is None or agent.browser.view_task.done()
                await page_b.get_by_role(
                    "button", name="Browser hier übernehmen", exact=True
                ).click()
                _, state_b, _ = await accepted(page_b, "B", seed["sessionId"])
                await page.get_by_role(
                    "button", name="Browser erneut verbinden", exact=True
                ).click()
                await offered(page, "A", seed["sessionId"], state_b["viewId"])
                await page.get_by_role("button", name="Browser hier übernehmen", exact=True).click()
                control, state, query = await accepted(page, "A", seed["sessionId"])
                await expect(
                    page_b.get_by_text(re.compile("Der Browser wurde in einer anderen Ansicht"))
                ).to_be_visible()
                for viewer in (page, page_b):
                    assert await viewer.evaluate("takeoverErrors") == [], await viewer.evaluate(
                        "takeoverErrors"
                    )
                assert not any(
                    item.get("error") for item in evidence["agentOperations"][operations_start:]
                )
                evidence["takeover"] = {
                    "browserId": browser_id,
                    "workerPid": worker_pid,
                    "siteNavigationAndDownload": "completed without cancellation",
                    "extraBrowserOpens": 0,
                    "graceCrossSessionRequiresClick": True,
                }
                await page_b.close()
                print(
                    "TAKEOVER PASS: A/B/A, same login and independent login, stale HTTP, "
                    "manual attachment, SPA grace, live site traffic",
                    flush=True,
                )
                await menu_action("Browser schließen")
                await expect(
                    page.get_by_role("button", name="Browser erneut verbinden", exact=True)
                ).to_be_visible()
                await expect(canvas).not_to_be_visible()
                async with asyncio.timeout(20):
                    while agent.browser.browser_id is not None:
                        await asyncio.sleep(0.1)
                    # The ID is cleared before worker shutdown and its RPC response finish.
                    async with agent.browser.lock:
                        pass
                async with page.expect_response(
                    lambda response: (
                        f"/integrations/browser/{integration_id}/status" in response.url
                    )
                ) as status_response:
                    await menu_action("Status prüfen")
                response = await status_response.value
                assert response.ok and (await response.json())["state"] == "ready"
                await page.get_by_role(
                    "button", name="Browser erneut verbinden", exact=True
                ).click()
                await expect(page.get_by_text("Manuelle Steuerung", exact=True)).to_be_visible()
                await expect(canvas).to_be_visible()
                control = latest_control("A")
                reopened = next(m for m in control["messages"] if m["type"] == "state")
                assert reopened["browserId"] != browser_id and reopened["viewId"] != state["viewId"]
                await navigate_url(fixture_url)
                async with asyncio.timeout(10):
                    while fixture_reports["/geometry"]["documentId"] == document_id:
                        await asyncio.sleep(0.1)
                assert fixture_reports["/geometry"]["previousProfile"] == "persistent-browser-e2e"
                evidence["persistentProfile"] = "localStorage survived explicit close and reopen"
                evidence["reconnect"] = "new real relay connected without restoring attachment"
                await menu_action("Browser schließen")
                assert any(item["binaryBytes"] > 10000 for item in evidence["websockets"])
                assert not evidence["errors"], evidence["errors"]
                await api("DELETE", f"/integrations/browser/{integration_id}")
                await api("DELETE", f"/execution-agents/{agent_id}")
                await browser.close()
                browser = page = None
                evidence["result"] = "PASS"
        except BaseException as error:
            evidence["result"] = "FAIL"
            evidence["failure"] = f"{type(error).__name__}: {error}"
            if agent is not None:
                evidence["failureRuntime"] = {
                    "browserId": agent.browser.browser_id,
                    "workerPid": agent.browser.worker.pid if agent.browser.worker else None,
                    "workerReturncode": agent.browser.worker.returncode
                    if agent.browser.worker
                    else None,
                }
            if page is not None:
                with contextlib.suppress(Exception):
                    await page.screenshot(
                        path=str(artifacts / "failure.png"), full_page=True, animations="disabled"
                    )
                    evidence["visibleText"] = await page.locator("body").inner_text()
                    evidence["nativeClipboard"] = await page.evaluate("""async () => ({
                        ...window.browserE2E, localClipboard: await navigator.clipboard.readText(),
                        focused: document.activeElement?.outerHTML
                    })""")
            raise
        finally:
            slow.set()
            handoff_load.set()
            handoff_download.set()
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if agent:
                await agent.browser.close()
                await agent.terminal.close_all()
                assert agent.browser.worker is None and agent.browser.view_task is None
            if browser:
                with contextlib.suppress(Exception):
                    await browser.close()
            await playwright.stop()
            server.close()
            await server.wait_closed()
            proxy.close()
            await proxy.wait_closed()
            evidence["processCleanup"] = (
                "isolated agent stopped; browser and terminal workers closed"
            )
            evidence["fixtureReports"] = fixture_reports
            evidence["fixtureDocuments"] = fixture_documents
            evidence["durationSeconds"] = round(asyncio.get_running_loop().time() - started, 2)
            (artifacts / "evidence.json").write_text(json.dumps(evidence, indent=2))
            print(f"RESULT: {evidence.get('result')} ({artifacts})", flush=True)


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode in {"seed", "cleanup"}:
        asyncio.run(database(mode))
    elif mode == "run":

        async def bounded_run():
            async with asyncio.timeout(350):
                await smoke(json.load(sys.stdin))

        asyncio.run(bounded_run())
    else:
        raise SystemExit("Expected seed, run, or cleanup")
