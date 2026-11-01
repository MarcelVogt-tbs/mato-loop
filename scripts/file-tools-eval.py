"""Run one real coding-tool and cache acceptance flow in the development stack."""

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID, uuid4

from sqlalchemy import select

from mato_loop_backend.db.session import AsyncSessionFactory, engine
from mato_loop_backend.models import Message
from mato_loop_backend.session_cache_eval import SessionCacheEvaluator
from mato_loop_backend.settings.models import BUILD_PROFILE_ID
from mato_loop_backend.settings.session_profiles import CODING_TEMPLATE_ID


async def run(args):
    evaluator = SessionCacheEvaluator(args)
    directory = f"/workspace/file-tools-acceptance-{uuid4().hex}"
    report = {"directory": directory, "checks": {}, "requests": []}
    session_id = None
    try:
        await evaluator.prepare()
        report["model"] = evaluator.model_metadata
        agents = await evaluator._json("GET", "/execution-agents")
        candidates = [agent for agent in agents["items"] if agent["connected"] and agent["homeDirectory"] == "/workspace"]
        if len(candidates) != 1:
            raise RuntimeError("Expected one connected development Agent with /workspace home.")
        agent_id = candidates[0]["id"]
        prompts = [
            f"Run this authorized file-tool acceptance test, only inside {directory}. "
            "Use create_directory, write_file, edit_file, apply_patch and read; no shell, delegation or other tools. "
            "Omit root on write_file/edit_file to exercise the computer default. "
            "First create the directory. Write main.txt with alpha=1 and keep=SAFE on separate lines, "
            "then overwrite it using write_file with alpha=2 and keep=SAFE. "
            "For the intentional repair probe, call edit_file exactly once with oldString=alpha=999 and newString=alpha=3. "
            "After that expected rejection, correct oldString to alpha=2 and apply alpha=3. "
            "Use apply_patch to change alpha=3 to alpha=4 and add extra.txt containing temporary. "
            "Read main.txt to verify alpha=4 and keep=SAFE. Finish with ACCEPTED-1.",
            f"Continue the same acceptance test inside {directory}. Use edit_file to change alpha=4 to alpha=5. "
            "Then apply_patch must move main.txt to renamed.txt, change alpha=5 to alpha=6, and delete extra.txt. "
            "Read renamed.txt to verify alpha=6 and keep=SAFE. No shell or delegation. Finish with ACCEPTED-2.",
            f"Read {directory}/renamed.txt once. Make no changes. If its contents are alpha=6 and keep=SAFE "
            "on separate lines, answer exactly ACCEPTED-3.",
        ]
        for index, prompt in enumerate(prompts, 1):
            if session_id is None:
                created = await evaluator._json("POST", "/sessions", payload={
                    "content": prompt, "run_id": str(uuid4()), "model_id": str(evaluator.model_id),
                    "session_template_id": str(CODING_TEMPLATE_ID),
                    "agent_profile_id": str(BUILD_PROFILE_ID), "memory_mode": "disabled",
                    "web_search_policy": "disabled", "preferred_execution_agent_id": agent_id,
                    "current_directory": "/workspace",
                })
                session_id = UUID(created["session"]["id"])
                evaluator.session_ids.append(session_id)
                run_id = UUID(created["runId"])
                report["sessionId"] = str(session_id)
            else:
                run_id = await evaluator._send(session_id, prompt)
            _, answer = await evaluator._wait_run(session_id, run_id)
            assert f"ACCEPTED-{index}" in answer.content, f"Turn {index} did not confirm acceptance."
            print(f"Turn {index} completed: {run_id}", flush=True)

        async with AsyncSessionFactory() as db:
            messages = list((await db.scalars(select(Message).where(
                Message.session_id == session_id,
            ).order_by(Message.created_at, Message.id))).all())
        calls = [call for message in messages for call in message.tool_calls or []]
        repairs = [message for message in messages if message.role == "tool"
                   and (message.provider_data or {}).get("_matoloop", {}).get("toolRepair")]
        assert {"write_file", "edit_file", "apply_patch", "read"} <= {call["name"] for call in calls}
        assert all("expectedSha256" not in call["arguments"] for call in calls)
        file_results = {message.tool_call_id: json.loads(message.content) for message in messages
                        if message.role == "tool" and (message.provider_data or {}).get("_matoloop", {}).get("toolName")
                        in {"write_file", "edit_file"}}
        writes = [call for call in calls if call["name"] == "write_file"
                  and file_results.get(call["id"], {}).get("written") == directory + "/main.txt"]
        assert len(writes) >= 2, "Create and overwrite were not both completed on main.txt."
        report["checks"]["createAndOverwrite"] = True
        for tool, result_field in (("write_file", "written"), ("edit_file", "edited")):
            assert any(call["name"] == tool and "root" not in call["arguments"]
                       and file_results.get(call["id"], {}).get(result_field)
                       for call in calls), f"No successful {tool} call exercised the default computer root."
        report["checks"]["defaultComputerRoots"] = True
        assert any(message.provider_data["_matoloop"].get("toolRepair") == "recovered"
                   and message.provider_data["_matoloop"].get("errorCode") == "file.editTextNotFound"
                   for message in repairs), "The deliberate edit error was not recorded as repaired."
        report["repairs"] = [{"toolCallId": message.tool_call_id,
                              "errorCode": message.provider_data["_matoloop"].get("errorCode"),
                              "state": message.provider_data["_matoloop"]["toolRepair"]} for message in repairs]
        for message in messages:
            data = message.provider_data or {}
            performance = data.get("_matoloop", {}).get("performance")
            if message.role == "assistant" and performance:
                report["requests"].append({"runId": data["_matoloop"].get("runId"),
                                           "usage": data.get("usage", {}), "performance": performance})
        report["checks"]["toolCallsAndRepair"] = True
        context = {"scope": "filesystem", "execution_agent_id": agent_id, "current_directory": "/workspace"}
        preview = await evaluator._json("GET", "/workspace/files/preview?" + urlencode({
            **context, "path": directory + "/renamed.txt",
        }))
        assert preview["content"] == "alpha=6\nkeep=SAFE\n", "Independent final-content check failed."
        listing = await evaluator._json("GET", "/workspace/entries?" + urlencode({**context, "path": directory}))
        assert [entry["name"] for entry in listing["entries"]] == ["renamed.txt"]
        report["checks"]["independentFilesystem"] = True
        names = [row["performance"].get("offeredToolNames") for row in report["requests"]]
        assert names and all(value == names[0] for value in names), "Offered tool order changed during acceptance."
        report["checks"]["stableToolOrder"] = True
        report["cache"] = [{"inputTokens": row["usage"].get("input_tokens"),
                            "cachedInputTokens": row["usage"].get("cached_input_tokens"),
                            "contextPreparationMs": row["performance"].get("contextPreparationMs")}
                           for row in report["requests"]]
        assert any((row["cachedInputTokens"] or 0) > 0 for row in report["cache"]), "No live cache reuse observed."
        report["checks"]["observedCacheReuse"] = True
        await evaluator._json("DELETE", "/workspace/entries?" + urlencode({**context, "path": directory}))
        report["checks"]["scratchRemoved"] = True
    except Exception as exc:
        report["failure"] = f"{type(exc).__name__}: {exc}"
    finally:
        report["cleanupFailures"] = await evaluator.cleanup()
        await engine.dispose()
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"checks": report["checks"], "cache": report.get("cache"),
                      "failure": report.get("failure"), "report": str(output)}, indent=2), flush=True)
    return int("failure" in report or bool(report["cleanupFailures"]))


if __name__ == "__main__":
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--model", default="openai-codex/gpt-6.1-sol-ultrafast")
    arguments.add_argument("--user-id")
    arguments.add_argument("--api-base", default="http://backend:6020/api")
    arguments.add_argument("--output", default="/workspace/data/file-tools-acceptance.json")
    arguments.set_defaults(adapter="openai-codex", recover=False, reasoning_effort=None, run_timeout=360)
    raise SystemExit(asyncio.run(run(arguments.parse_args())))
