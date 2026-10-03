"""Opt-in real browser-control E2E; uses test_browser_e2e's isolated sandbox harness.

Use the seed/cleanup commands documented in test_browser_e2e.py, substituting this
filename for the run command. In the backend, pass the seed JSON to this file's
prepare mode before run, and remove-storage mode before cleanup. The original
UI-only seed does not provision the session storage needed by actual agent runs.
A Compose one-off agent container also works; keep
it until its /tmp/browser-e2e-<user ID> artifacts have been copied out.
Only this user's personal provider is configured. Its local deterministic HTTP
stub replaces the LLM transport, not runtime, approvals, SSE, WebSockets or noVNC.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import socket
import sys
from urllib.parse import parse_qs, urlsplit

from test_browser_e2e import database, smoke


async def storage(mode):
    from uuid import UUID

    from mato_loop_backend.db.session import AsyncSessionFactory
    from mato_loop_backend.files.storage import SessionStorage
    from mato_loop_backend.models import ChatSession, UserAccount
    from mato_loop_backend.security import verify_password

    seed = json.load(sys.stdin)
    async with AsyncSessionFactory() as db:
        user = await db.get(UserAccount, UUID(seed["userId"]))
        assert user and user.username == f"browser-e2e-{user.id}" and not user.is_admin
        assert verify_password(seed["password"], user.password_hash)
        for key in ("sessionId", "secondSessionId"):
            session = await db.get(ChatSession, UUID(seed[key]))
            assert session and session.user_id == user.id and session.project_id is None
            service = SessionStorage()
            if mode == "prepare":
                service.provision(session.id, None)
            else:
                service.remove_session(session.id, None)
    print(f"STORAGE PASS: {mode}, only the two owned test sessions")


async def control_scenario(
    page,
    api,
    agent,
    seed,
    frontend,
    fixture_url,
    artifacts,
    evidence,
    fixture_reports,
):
    from playwright.async_api import expect

    evidence["modelRequests"] = []
    evidence["controlLookups"] = []
    evidence["handshakes"] = []
    evidence["attachFrames"] = []
    evidence["documents"] = []
    tasks = []

    async def model(reader, writer):
        try:
            headers = (await reader.readuntil(b"\r\n\r\n")).decode("latin1")
            assert headers.splitlines()[0] == "POST /v1/chat/completions HTTP/1.1"
            length = next(
                int(line.split(":", 1)[1])
                for line in headers.splitlines()
                if line.lower().startswith("content-length:")
            )
            assert length < 2_000_000
            request = json.loads(await reader.readexactly(length))
            messages = request["messages"]
            tools = [item["function"] for item in request.get("tools", [])]
            tool_results = [item for item in messages if item["role"] == "tool"]
            record = {
                "model": request["model"],
                "toolNames": [item["name"] for item in tools],
                "toolResults": tool_results,
            }
            evidence["modelRequests"].append(record)
            if tool_results:
                assert any(json.loads(item["content"]).get("ok") is True for item in tool_results)
                delta = {"role": "assistant", "content": "Browser control E2E complete."}
                finish = "stop"
            else:
                tool = next(item for item in tools if "browser_request_control" in item["name"])
                record["schema"] = tool
                delta = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "control_e2e_call",
                            "type": "function",
                            "function": {
                                "name": tool["name"],
                                "arguments": json.dumps({"reason": "Isolated browser control E2E"}),
                            },
                        }
                    ],
                }
                finish = "tool_calls"
            assert request["stream"] is True
            body = (
                b"".join(
                    b"data: "
                    + json.dumps(
                        {
                            "id": "control-e2e",
                            "object": "chat.completion.chunk",
                            "model": request["model"],
                            "choices": [{"index": 0, "delta": data, "finish_reason": reason}],
                        }
                    ).encode()
                    + b"\n\n"
                    for data, reason in ((delta, None), ({}, finish))
                )
                + b"data: [DONE]\n\n"
            )
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        except BaseException as error:
            evidence.setdefault("stubErrors", []).append(f"{type(error).__name__}: {error}")
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    server = await asyncio.start_server(model, "0.0.0.0", 0)
    base_url = f"http://{socket.gethostbyname(socket.gethostname())}:{server.sockets[0].getsockname()[1]}/v1"

    async def lookup(response):
        if "/browsers/control-requests?" in response.url:
            evidence["controlLookups"].append(
                {
                    "url": response.url,
                    "status": response.status,
                    "body": await response.json(),
                }
            )

    page.on("response", lambda response: tasks.append(asyncio.create_task(lookup(response))))

    def observe_socket(connection):
        if "/api/browsers/" not in connection.url or "/view?" in connection.url:
            return

        def sent(payload):
            if isinstance(payload, str) and json.loads(payload).get("type") == "attach.requested":
                view = next(
                    item for item in reversed(evidence["websockets"]) if "/view?" in item["url"]
                )
                evidence["attachFrames"].append(
                    {
                        **json.loads(payload),
                        "viewUrl": view["url"],
                        "rfbBytesBeforeAttach": view["binaryBytes"],
                    }
                )

        connection.on("framesent", sent)

    page.on("websocket", observe_socket)
    page.on(
        "request",
        lambda request: evidence["documents"].append(request.url)
        if request.is_navigation_request()
        else None,
    )
    await page.add_init_script("""
        window.controlEvents = [];
        globalThis.EventSource = new Proxy(EventSource, {
            construct(target, args, receiver) {
                const source = Reflect.construct(target, args, receiver);
                source.addEventListener('browser.control.requested', event =>
                    controlEvents.push(JSON.parse(event.data)));
                return source;
            }
        });
    """)

    async def wait_for(predicate):
        async with asyncio.timeout(30):
            while not predicate():
                await asyncio.sleep(0.1)

    def control():
        return next(
            item for item in reversed(evidence["websockets"]) if "/view?" not in item["url"]
        )

    def state():
        return next(item for item in reversed(control()["messages"]) if item["type"] == "state")

    def navigation():
        return next(
            (item for item in reversed(control()["messages"]) if item["type"] == "navigation"), None
        )

    async def render(name, width, height, theme):
        await page.set_viewport_size({"width": width, "height": height})
        await page.emulate_media(color_scheme=theme)
        await expect(page.locator("body")).to_have_class(re.compile(rf".*body--{theme}.*"))
        await expect(page.locator(".browser-app canvas")).to_be_visible()
        await page.wait_for_function("""() => {
            const host = document.querySelector('.browser-app__view');
            const canvas = host?.querySelector('canvas');
            if (!canvas) return false;
            const h = host.getBoundingClientRect(), c = canvas.getBoundingClientRect();
            return c.x >= 0 && c.y >= 0 && c.right <= innerWidth + 1
                && c.bottom <= innerHeight + 1 && c.width > 100 && c.height > 100
                && canvas.width === Math.floor(h.width) && canvas.height === Math.floor(h.height)
                && document.documentElement.scrollWidth <= innerWidth;
        }""")
        async with asyncio.timeout(30):
            while True:
                box = await page.locator(".browser-app canvas").bounding_box()
                if fixture_reports.get("/geometry", {}).get("screen") == {
                    "width": int(box["width"]),
                    "height": int(box["height"]),
                }:
                    break
                await asyncio.sleep(0.1)
        # The resize acknowledgement precedes Chromium's next framebuffer paint.
        await asyncio.sleep(0.5)
        await page.screenshot(path=str(artifacts / f"{name}.png"), animations="disabled")
        evidence["cases"].append({"name": name, "canvas": box})
        print(f"RENDER PASS: {name}", flush=True)

    async def request_control(session_id, trust):
        before = len(evidence["websockets"])
        await page.locator(".chat-composer textarea").fill("Request browser control for this chat.")
        await page.get_by_role("button", name="Nachricht senden", exact=True).click()
        allow = page.get_by_role("button", name="Für diesen Lauf erlauben", exact=True)
        await expect(allow).to_be_visible()
        approvals = await api("GET", "/approvals/pending")
        assert len(approvals) == 1 and approvals[0]["sourceSession"]["id"] == session_id, approvals
        assert approvals[0]["payload"]["trustLevel"] == trust
        assert approvals[0]["capability"] == "browser.control"
        evidence.setdefault("approvals", []).append({"trust": trust, "approval": approvals[0]})
        assert len(evidence["websockets"]) == before, "Browser opened before approval"
        await page.screenshot(path=str(artifacts / f"approval-{trust}.png"), animations="disabled")
        async with page.expect_response(
            lambda r: "/decisions" in r.url and r.request.method == "POST"
        ) as decision:
            await allow.click()
        response = await decision.value
        assert response.ok and response.request.post_data_json["decision"] == "allow_once"
        await expect(page.get_by_role("switch", name="Agent verbinden")).to_be_checked()
        await expect(page.get_by_text("Browser control E2E complete.", exact=True)).to_be_visible()
        await expect(page.locator(".browser-app canvas")).to_be_visible()
        assert parse_qs(urlsplit(page.url).query)["session"] == [session_id]
        current = control()
        query = parse_qs(urlsplit(current["url"]).query)
        request_id = query["controlRequestId"][0]
        assert query["sessionId"] == [session_id]
        assert {"type": "attach.requested", "requestId": request_id} in current["sent"]
        assert any(
            item["requestId"] == request_id and item["rfbBytesBeforeAttach"] > 24
            for item in evidence["attachFrames"]
        )
        assert not any(item["type"] == "takeover" for item in current["sent"])
        assert state()["attached"] is True
        await wait_for(
            lambda: any(
                item["body"].get("request", {}).get("id") == request_id
                for item in evidence["controlLookups"]
                if item["body"].get("request")
            )
        )
        events = await page.evaluate("controlEvents")
        assert any(item["request"]["id"] == request_id for item in events), events
        assert any(item["binaryBytes"] > 1000 for item in evidence["websockets"][before:])
        evidence["handshakes"].append(
            {"trust": trust, "requestId": request_id, "state": state(), "sse": events[-1]}
        )
        print(
            f"HANDSHAKE PASS: {trust}, real approval/GET/SSE/WS/noVNC/attach.requested", flush=True
        )
        return state()

    try:
        provider = await api(
            "POST",
            "/providers/openai-compatible?scope=personal",
            {
                "name": "Isolated browser E2E stub",
                "baseUrl": base_url,
                "trustLevel": "public",
                "pseudonymizationEnabled": False,
                "memoryPolicy": "deny",
            },
        )
        for session_id, trust in (
            (seed["sessionId"], "public"),
            (seed["secondSessionId"], "private"),
        ):
            settings = await api(
                "POST",
                f"/providers/configuration/{provider['providerId']}/models?scope=personal",
                {
                    "modelId": f"browser-e2e-{trust}",
                    "name": f"Browser E2E {trust}",
                    "trustLevel": trust,
                    "enabled": True,
                },
            )
            model_id = next(
                item["id"]
                for item in settings["models"]
                if item["modelId"] == f"browser-e2e-{trust}"
            )
            await api(
                "PATCH",
                f"/sessions/{session_id}",
                {
                    "model_id": model_id,
                    "control_center": {
                        "tabs": [
                            {"app_id": "session", "pinned": False},
                            *(
                                [{"app_id": "browser", "pinned": False}]
                                if trust == "private"
                                else []
                            ),
                        ],
                        "active_app_id": "browser" if trust == "private" else "session",
                        "open": trust == "private",
                    },
                },
            )
        await page.goto(frontend + f"/?session={seed['sessionId']}")
        fresh = await request_control(seed["sessionId"], "public")
        await wait_for(lambda: navigation() is not None)
        assert [item["url"] for item in navigation()["tabs"]] == ["about:blank"], navigation()
        assert evidence["fixtureRequests"] == []
        assert not any(
            item["operation"] == "integration.browser.execute"
            for item in evidence["agentOperations"]
        )
        assert len(evidence["documents"]) == 1, evidence["documents"]

        await page.get_by_role("button", name="Manuell übernehmen", exact=True).click()
        await expect(page.get_by_text("Manuelle Steuerung", exact=True)).to_be_visible()
        address = page.get_by_role("textbox", name="Webadresse", exact=True)
        for path in ("one", "two"):
            if path == "two":
                await page.get_by_role("button", name="Neuer Tab", exact=True).click()
            await expect(page.get_by_role("button", name="Neuer Tab", exact=True)).to_be_enabled()
            await address.fill(fixture_url + path)
            await address.press("Enter")
            await wait_for(
                lambda path=path: navigation()
                and not navigation()["loading"]
                and any(tab["url"] == fixture_url + path for tab in navigation()["tabs"])
            )
        await page.get_by_role("switch", name="Agent verbinden").click()
        await expect(page.get_by_role("switch", name="Agent verbinden")).to_be_checked()
        original = {"state": state(), "navigation": navigation(), "pid": agent.browser.worker.pid}
        original_control = control()
        original_view = evidence["websockets"][-1]
        assert "/view?" in original_view["url"]
        socket_count = len(evidence["websockets"])
        requests_before = list(evidence["fixtureRequests"])
        await page.evaluate("window.retainedCanvas = document.querySelector('.browser-app canvas')")
        for size, width, height in (("desktop", 1440, 1000), ("mobile", 390, 844)):
            for theme in ("light", "dark"):
                await render(f"attached-{size}-{theme}", width, height, theme)
        await page.set_viewport_size({"width": 1440, "height": 1000})
        await page.get_by_role("button", name="Navigation öffnen", exact=True).click()
        sent_before = len(original_control["sent"])
        await page.locator(".session-list-item").filter(has_text="Browser UI smoke B").click()
        await expect(page).to_have_url(re.compile(seed["secondSessionId"]))
        await expect(
            page.get_by_role("button", name="Browser hier übernehmen", exact=True)
        ).to_be_visible()
        await expect(page.get_by_role("switch", name="Agent verbinden")).not_to_be_checked()
        await expect(page.get_by_role("switch", name="Agent verbinden")).to_be_disabled()
        await expect(page.get_by_role("button", name="Neuer Tab", exact=True)).to_be_disabled()
        await expect(page.locator(".browser-app canvas")).to_have_count(0)
        await page.screenshot(path=str(artifacts / "occupied-session-b.png"), animations="disabled")
        started = asyncio.get_running_loop().time()
        await asyncio.sleep(35)
        assert not original_control.get("closed") and not original_view.get("closed")
        assert state()["attached"] is True and agent.browser.worker.pid == original["pid"]
        await page.locator(".session-list-item").filter(has_text="Browser UI smoke A").click()
        await expect(page).to_have_url(re.compile(seed["sessionId"]))
        await expect(page.locator(".browser-app canvas")).to_be_visible()
        await expect(page.get_by_role("switch", name="Agent verbinden")).to_be_checked()
        assert await page.evaluate(
            "retainedCanvas === document.querySelector('.browser-app canvas')"
        )
        assert len(evidence["websockets"]) == socket_count
        assert state()["browserId"] == original["state"]["browserId"] == fresh["browserId"]
        assert state()["viewId"] == original["state"]["viewId"]
        assert navigation()["tabs"] == original["navigation"]["tabs"]
        assert navigation()["activeTabId"] == original["navigation"]["activeTabId"]
        assert evidence["fixtureRequests"] == requests_before
        assert not any(
            item["type"] in {"release", "close", "detach"}
            for item in original_control["sent"][sent_before:]
        )
        evidence["retention"] = {
            "secondsAway": asyncio.get_running_loop().time() - started,
            **original,
            "sameCanvas": True,
            "newSockets": 0,
            "newNavigations": 0,
        }
        await render("retained-desktop-dark", 1440, 1000, "dark")
        print(
            "RETENTION PASS: A -> B -> A after 35 seconds, "
            "same browser/view/tabs/canvas/attachment",
            flush=True,
        )

        await page.locator(".session-list-item").filter(has_text="Browser UI smoke B").click()
        await expect(page).to_have_url(re.compile(seed["secondSessionId"]))
        taken = await request_control(seed["secondSessionId"], "private")
        await wait_for(lambda: navigation() is not None)
        assert (
            taken["browserId"] == fresh["browserId"]
            and taken["viewId"] != original["state"]["viewId"]
        )
        assert agent.browser.worker.pid == original["pid"]
        assert navigation()["tabs"] == original["navigation"]["tabs"]
        assert evidence["fixtureRequests"] == requests_before
        assert (
            sum(
                item["operation"] == "integration.browser.open"
                for item in evidence["agentOperations"]
            )
            == 1
        )
        assert len(evidence["documents"]) == 1
        await render("takeover-desktop-light", 1440, 1000, "light")
        await render("takeover-mobile-dark", 390, 844, "dark")
        assert not evidence["errors"] and not evidence.get("stubErrors"), evidence
        await expect(page.locator(".q-notification.bg-negative")).to_have_count(0)
        print(
            "TAKEOVER PASS: approved private request reused browser and tabs "
            "without takeover click/navigation",
            flush=True,
        )
        wire_errors = [
            message
            for connection in evidence["websockets"]
            for message in connection["messages"]
            if message["type"] == "error"
        ]
        assert not wire_errors, wire_errors
    finally:
        await asyncio.gather(*tasks, return_exceptions=True)
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    if sys.argv[1] in {"seed", "cleanup"}:
        asyncio.run(database(sys.argv[1]))
    elif sys.argv[1] == "run":
        asyncio.run(smoke(json.load(sys.stdin), scenario=control_scenario))
    elif sys.argv[1] in {"prepare", "remove-storage"}:
        asyncio.run(storage(sys.argv[1]))
    else:
        raise SystemExit("Expected seed, prepare, run, remove-storage, or cleanup")
