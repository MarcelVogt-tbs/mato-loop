"""Opt-in real WebUI/backend/Agent page-stream smoke test.

Seed/cleanup run in the development backend; run executes as matoloop-browser in
the development Agent. Pass the seed JSON on stdin. Only the isolated test owner
is created/deleted. Artifacts remain under /tmp/browser-e2e-<user ID> on the Agent.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4


async def database(mode):
    from mato_loop_backend.db.session import AsyncSessionFactory
    from mato_loop_backend.files.storage import SessionStorage
    from mato_loop_backend.models import ChatSession, UserAccount
    from mato_loop_backend.security import hash_password, verify_password
    from sqlalchemy import delete

    async with AsyncSessionFactory() as db:
        if mode == "seed":
            user_id, session_id, second = uuid4(), uuid4(), uuid4()
            password = secrets.token_urlsafe(32)
            username = f"browser-e2e-{user_id}"
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
            for identifier in (session_id, second):
                db.add(
                    ChatSession(
                        id=identifier,
                        user_id=user_id,
                        title="Isolated browser test",
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
                        secondSessionId=str(second),
                        username=username,
                        password=password,
                    )
                )
            )
        else:
            seed = json.load(sys.stdin)
            user = await db.get(UserAccount, UUID(seed["userId"]))
            assert user and user.username == f"browser-e2e-{user.id}" and not user.is_admin
            assert verify_password(seed["password"], user.password_hash)
            for key in ("sessionId", "secondSessionId"):
                session = await db.get(ChatSession, UUID(seed[key]))
                assert session and session.user_id == user.id and session.project_id is None
                SessionStorage().remove_session(session.id, None)
            await db.execute(delete(UserAccount).where(UserAccount.id == user.id))
            await db.commit()
            print("CLEANUP PASS")


async def smoke(seed):
    from playwright.async_api import async_playwright, expect

    from matoloop_agent.config import AgentConfig
    from matoloop_agent.connection import MatoLoopAgent

    assert os.getuid() != 0, "Run as matoloop-browser with the Chromium sandbox enabled"
    expect.set_options(timeout=30000)
    target = os.environ.get("BROWSER_E2E_FRONTEND", "http://frontend:10020")
    backend = os.environ.get("BROWSER_E2E_BACKEND", "ws://backend:6020")
    artifacts = Path("/tmp") / f"browser-e2e-{UUID(seed['userId'])}"
    artifacts.mkdir(mode=0o700, exist_ok=False)
    reports, errors = {}, []

    async def forward(reader, writer):
        remote, destination = await asyncio.open_connection(
            urlsplit(target).hostname, urlsplit(target).port
        )

        async def pump(source, sink):
            try:
                while data := await source.read(65536):
                    sink.write(data)
                    await sink.drain()
            except (ConnectionError, OSError):
                pass
            finally:
                sink.close()

        await asyncio.gather(pump(reader, destination), pump(remote, writer))

    async def fixture(reader, writer):
        try:
            header = (await reader.readuntil(b"\r\n\r\n")).decode("latin1")
            method, path, _ = header.splitlines()[0].split()
            if method == "POST":
                length = int(
                    next(
                        line.split(":", 1)[1]
                        for line in header.splitlines()
                        if line.lower().startswith("content-length:")
                    )
                )
                assert length < 65536
                reports[path] = json.loads(await reader.readexactly(length))
            body = b"""<!doctype html><meta charset='utf-8'><title>Isolated browser fixture</title>
                <body style='margin:0;font:24px sans-serif;background:#f8fafc;color:#111827'>
                <h1 style='background:#16a34a;color:white;padding:20px'>PAGE STREAM</h1>
                <input id='name' aria-label='Name' style='font:24px sans-serif;width:80%'>
                <style>button:hover{background:rgb(255, 200, 0);cursor:pointer}</style>
                <button id='confirm' onclick="fetch('/dialog',{method:'POST',
                    body:JSON.stringify(prompt('Answer?','Default'))})">Prompt</button>
                <p id='result'>Selectable text</p>
                <iframe src='http://localhost:FRAME_PORT/frame'
                    style='display:block;width:280px;height:42px;border:3px solid'></iframe><script>
                const report=(path,value)=>fetch(path,{method:'POST',body:JSON.stringify(value)});
                document.querySelector('input').oninput=e=>report('/input',e.target.value);
                document.querySelector('input').onclick=()=>report('/focus',true);
                document.addEventListener('focusin',e=>report('/focused',e.target.id));
                const button=document.querySelector('button');
                for(const event of ['pointerenter','pointerleave']) button.addEventListener(event,
                    ()=>report('/hover',getComputedStyle(button).backgroundColor));
                const geometry=()=>report('/geometry',{width:innerWidth,height:innerHeight,
                    frame:document.querySelector('iframe').getBoundingClientRect().toJSON(),
                    text:document.querySelector('p').getBoundingClientRect().toJSON(),
                    input:document.querySelector('input').getBoundingClientRect().toJSON(),
                    button:document.querySelector('button').getBoundingClientRect().toJSON()});
                onresize=geometry;geometry();
                report('/profile',localStorage.getItem('owner'));
                localStorage.setItem('owner','this-chat');document.cookie='owner=this-chat';
                </script>"""
            if path == "/frame":
                body = b"""<!doctype html><style>html,body{margin:0;height:100%}
                    button{width:100%;height:100%;cursor:pointer}</style><button>Continue</button>"""
            body = body.replace(b"FRAME_PORT", str(server.sockets[0].getsockname()[1]).encode())
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                b"Connection: close\r\n" + f"Content-Length: {len(body)}\r\n\r\n".encode() + body
            )
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    proxy = await asyncio.start_server(forward, "127.0.0.1", 0)
    server = await asyncio.start_server(fixture, "127.0.0.1", 0)
    frontend = f"http://127.0.0.1:{proxy.sockets[0].getsockname()[1]}"
    fixture_url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/"
    agent = task = browser = page = None
    with tempfile.TemporaryDirectory(prefix="browser-e2e-") as temporary:
        home = Path(temporary)
        async with async_playwright() as playwright:
            try:
                async with asyncio.timeout(180):
                    browser = await playwright.chromium.launch(
                        channel="chromium",
                        headless=True,
                        chromium_sandbox=True,
                        env={**os.environ, "HOME": str(home)},
                    )
                    context = await browser.new_context(viewport={"width": 1440, "height": 1000})
                    await context.grant_permissions(
                        ["clipboard-read", "clipboard-write"], origin=frontend
                    )

                    async def api(method, path, data=None):
                        response = await context.request.fetch(
                            frontend + "/api" + path, method=method, data=data
                        )
                        assert response.ok, (method, path, response.status, await response.text())
                        return await response.json() if response.status != 204 else None

                    await api(
                        "POST",
                        "/auth/login",
                        {"username": seed["username"], "password": seed["password"]},
                    )
                    pairing = await api("POST", "/execution-agents", {"alias": seed["username"]})
                    agent_id = pairing["agent"]["id"]
                    key = secrets.token_urlsafe(48)
                    response = await context.request.post(
                        frontend + "/api/execution-agents/pair",
                        headers={"Authorization": "Bearer " + pairing["pairingToken"]},
                        data={
                            "agentId": agent_id,
                            "accessKeyHash": hashlib.sha256(key.encode()).hexdigest(),
                        },
                    )
                    assert response.status == 204
                    config = home / "config.json"
                    config.write_text(
                        json.dumps(
                            {
                                "backendUrl": backend + "/api/execution-agents/connect",
                                "agentId": agent_id,
                                "allowInsecure": True,
                                "homeDirectory": str(home),
                            }
                        )
                    )
                    agent = MatoLoopAgent(AgentConfig.load(config), key)
                    task = asyncio.create_task(agent.run_forever())
                    while not (await api("GET", f"/execution-agents/{agent_id}"))["connected"]:
                        await asyncio.sleep(0.1)
                    await api(
                        "POST",
                        "/integrations/browser",
                        {
                            "name": "Isolated test browser",
                            "executionAgentId": agent_id,
                            "enabled": True,
                            "config": {},
                        },
                    )
                    for session_id in (seed["sessionId"], seed["secondSessionId"]):
                        await api(
                            "PATCH",
                            f"/sessions/{session_id}",
                            {"preferred_execution_agent_id": agent_id},
                        )
                    page = await context.new_page()
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    await page.goto(
                        frontend + f"/?session={seed['sessionId']}&controlCenter=browser"
                    )
                    canvas = page.locator(".browser-app canvas")
                    await expect(canvas).to_be_visible()
                    address = page.get_by_role("textbox", name="Webadresse", exact=True)
                    await address.fill(fixture_url)
                    await address.press("Enter")
                    await expect(address).to_have_value(fixture_url)
                    async with asyncio.timeout(20):
                        while "/profile" not in reports:
                            await asyncio.sleep(0.05)
                    browser_store = await page.evaluate_handle("""async () => {
                        const {useBrowserStore}=await import('/src/stores/browser.js');
                        return useBrowserStore();
                    }""")
                    await page.wait_for_function(
                        """browser => {
                        const view=browser.viewer;
                        return browser.manual && view?.drawn &&
                            view.drawn.inputEpoch===view.state?.inputEpoch &&
                            view.drawn.tabId===view.state?.tabId;
                    }""",
                        arg=browser_store,
                    )
                    box = await canvas.bounding_box()
                    geometry = reports["/geometry"]
                    scale = min(
                        box["width"] / geometry["width"], box["height"] / geometry["height"]
                    )
                    await canvas.click(
                        position={
                            "x": (box["width"] - geometry["width"] * scale) / 2
                            + (geometry["input"]["x"] + 10) * scale,
                            "y": (box["height"] - geometry["height"] * scale) / 2
                            + (geometry["input"]["y"] + 10) * scale,
                        }
                    )
                    await page.keyboard.type("Hello browser")
                    async with asyncio.timeout(10):
                        while reports.get("/input") != "Hello browser":
                            await asyncio.sleep(0.05)
                    left = box["x"] + (box["width"] - geometry["width"] * scale) / 2
                    top = box["y"] + (box["height"] - geometry["height"] * scale) / 2
                    field = geometry["input"]
                    await page.mouse.move(
                        left + (field["x"] + field["width"] - 10) * scale,
                        top + (field["y"] + 10) * scale,
                    )
                    await page.mouse.down()
                    await page.mouse.move(
                        left + (field["x"] + 3) * scale,
                        top + (field["y"] + 10) * scale,
                        steps=10,
                    )
                    await page.mouse.up()
                    await page.evaluate("() => navigator.clipboard.writeText('local sentinel')")
                    await page.keyboard.press("Control+c")
                    async with asyncio.timeout(10):
                        while (
                            await page.evaluate("() => navigator.clipboard.readText()")
                            != "Hello browser"
                        ):
                            await asyncio.sleep(0.05)
                    await page.evaluate("() => navigator.clipboard.writeText('Grüße 🌍')")
                    await page.keyboard.press("Control+v")
                    async with asyncio.timeout(10):
                        while reports.get("/input") != "Grüße 🌍":
                            await asyncio.sleep(0.05)
                    await page.evaluate("() => navigator.clipboard.writeText('local sentinel')")
                    await page.keyboard.press("Meta+a")
                    await page.keyboard.press("Meta+c")
                    async with asyncio.timeout(10):
                        while (
                            await page.evaluate("() => navigator.clipboard.readText()")
                            != "Grüße 🌍"
                        ):
                            await asyncio.sleep(0.05)
                    await page.keyboard.press("Tab")
                    async with asyncio.timeout(10):
                        while reports.get("/focused") != "confirm":
                            await asyncio.sleep(0.05)
                    await page.keyboard.press("Shift+Tab")
                    async with asyncio.timeout(10):
                        while reports.get("/focused") != "name":
                            await asyncio.sleep(0.05)
                    text = geometry["text"]
                    await page.mouse.move(
                        left + (text["x"] + 10) * scale, top + (text["y"] + 10) * scale
                    )
                    await expect(canvas).to_have_css("cursor", "text")
                    frame = geometry["frame"]
                    await page.mouse.move(
                        left + (frame["x"] + 10) * scale, top + (frame["y"] + 10) * scale
                    )
                    await expect(canvas).to_have_css("cursor", "pointer")
                    button = geometry["button"]
                    await page.mouse.move(
                        left + (button["x"] + 10) * scale, top + (button["y"] + 10) * scale
                    )
                    await expect(canvas).to_have_css("cursor", "pointer")
                    async with asyncio.timeout(10):
                        while reports.get("/hover") != "rgb(255, 200, 0)":
                            await asyncio.sleep(0.05)
                    await address.hover()
                    async with asyncio.timeout(10):
                        while reports.get("/hover") == "rgb(255, 200, 0)":
                            await asyncio.sleep(0.05)
                    await page.keyboard.press("Meta+l")
                    await expect(address).to_be_focused()
                    await canvas.click(
                        position={
                            "x": (box["width"] - geometry["width"] * scale) / 2
                            + (geometry["input"]["x"] + 10) * scale,
                            "y": (box["height"] - geometry["height"] * scale) / 2
                            + (geometry["input"]["y"] + 10) * scale,
                        }
                    )
                    await page.keyboard.press("Tab")
                    await page.keyboard.press("Space")
                    dialog = page.get_by_role("dialog")
                    await expect(dialog).to_be_visible()
                    await page.reload()
                    await dialog.get_by_role("textbox", name="Antwort").fill("Confirmed")
                    await dialog.get_by_role("button", name="OK", exact=True).click()
                    async with asyncio.timeout(10):
                        while reports.get("/dialog") != "Confirmed":
                            await asyncio.sleep(0.05)
                    await expect(dialog).not_to_be_visible()
                    activity = """async busy => {
                        const {useBrowserStore}=await import('/src/stores/browser.js');
                        const {useChatStore}=await import('/src/features/chat/store.js');
                        const browser=useBrowserStore();
                        browser.busy=busy;browser.updateViewOnly();
                        useChatStore().running=busy;
                    }"""
                    for width, height, dark in ((1440, 1000, False), (390, 844, True)):
                        await page.set_viewport_size({"width": width, "height": height})
                        await page.emulate_media(color_scheme="dark" if dark else "light")
                        await expect(canvas).to_be_in_viewport()
                        async with asyncio.timeout(10):
                            while reports["/geometry"]["width"] > width:
                                await asyncio.sleep(0.05)
                        await page.screenshot(
                            path=str(artifacts / f"viewer-{width}.png"), animations="disabled"
                        )
                        idle = await address.bounding_box()
                        await page.evaluate(activity, True)
                        if width >= 1024:
                            await page.evaluate("""async () => {
                                const {useChatStore}=await import('/src/features/chat/store.js');
                                useChatStore().setDraft('Pending follow-up');
                            }""")
                            await expect(
                                page.get_by_role("button", name="Agent-Anfrage stoppen", exact=True)
                            ).to_be_visible()
                        await expect(page.get_by_text("Agent aktiv")).to_be_visible()
                        active = await address.bounding_box()
                        assert active == idle, (
                            f"Agent activity shifted the toolbar: {idle} -> {active}"
                        )
                        await page.screenshot(path=str(artifacts / f"viewer-{width}-agent.png"))
                        if width < 1024:
                            await (
                                page.locator("#control-center-panel")
                                .get_by_role("button", name="Agent-Anfrage stoppen", exact=True)
                                .click()
                            )
                            await page.wait_for_function("""async () => {
                                const {useChatStore}=await import('/src/features/chat/store.js');
                                return !useChatStore().running;
                            }""")
                        await page.evaluate(activity, False)
                    await page.goto(
                        frontend + f"/?session={seed['secondSessionId']}&controlCenter=browser"
                    )
                    await expect(canvas).to_be_visible()
                    reports.pop("/profile")
                    await address.fill(fixture_url)
                    await address.press("Enter")
                    async with asyncio.timeout(20):
                        while "/profile" not in reports:
                            await asyncio.sleep(0.05)
                    assert reports["/profile"] is None, "Another chat inherited website storage"
                    confirmation = page.get_by_role("dialog").filter(
                        has_text="ungespeicherte Eingaben"
                    )
                    for decision in ("Abbrechen", "Schließen"):
                        await page.get_by_role("button", name="Weitere Browseraktionen").click()
                        await page.get_by_text("Browser schließen", exact=True).click()
                        await expect(confirmation).to_be_visible()
                        await confirmation.get_by_role("button", name=decision, exact=True).click()
                        await expect(confirmation).not_to_be_visible()
                        if decision == "Abbrechen":
                            await expect(canvas).to_be_visible()
                        else:
                            await expect(canvas).to_have_count(0)
                    assert not errors, errors
                    print(
                        "PASS: keyboard, clipboard, separate chats, close confirmation; "
                        f"artifacts: {artifacts}"
                    )
            finally:
                if page is not None:
                    with contextlib.suppress(Exception):
                        await page.screenshot(path=str(artifacts / "final.png"))
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
                if browser is not None:
                    await browser.close()
                server.close()
                proxy.close()
                await asyncio.gather(server.wait_closed(), proxy.wait_closed())


if __name__ == "__main__":
    mode = sys.argv[1]
    asyncio.run(smoke(json.load(sys.stdin)) if mode == "run" else database(mode))
