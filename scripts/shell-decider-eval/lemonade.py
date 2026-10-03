"""JevK5 decision readout through Lemonade's OpenAI-compatible completion API."""

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from jevk5.gguf import JevK5GGUF

MODEL = "Shell-Eval-JevK5-9B-Q8"
CHECKPOINT = "alibiserikbay/JevK5-GGUF:jevk5-9b-v0.3.3-Q8_0.gguf"


class LemonadeJevK5(JevK5GGUF):
    def __init__(self, url):
        super().__init__(url=url.rstrip("/") + "/api/v1", temperature=1.316,
                         knockout_temperature=1.05, top_k=100)
        self.audit = []
        self.last_response = None

    def request(self, path, body=None, timeout=600):
        start = time.perf_counter()
        request = urllib.request.Request(
            self.url + path, data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Lemonade {path}: HTTP {exc.code}: {exc.read().decode()}") from exc
        if isinstance(result, dict) and result.get("error"):
            raise RuntimeError(f"Lemonade {path}: {result['error']}")
        self.audit.append({"path": path, "body": body, "seconds": time.perf_counter() - start,
                           "response": result})
        return result

    def prepare(self):
        health = self.request("/health")
        info = self.request("/system-info")
        self.request("/models")
        print(f"Lemonade {health['version']}; {info['Processor']}", flush=True)
        backend = info["recipes"]["llamacpp"]["backends"]["vulkan"]
        if backend["state"] != "installed":
            print("Updating the Vulkan llama.cpp backend", flush=True)
            self.request("/install", {"recipe": "llamacpp", "backend": "vulkan"})
        print(f"Downloading {CHECKPOINT}", flush=True)
        job = self.request("/pull", {"model_name": "user." + MODEL,
                                    "checkpoint": CHECKPOINT, "recipe": "llamacpp",
                                    "stream": True, "subscribe": False})
        deadline = time.monotonic() + 1800
        while not job.get("complete"):
            if job.get("status") in {"error", "cancelled", "paused"}:
                raise RuntimeError(f"Model download did not complete: {job}")
            if time.monotonic() > deadline:
                raise TimeoutError("Lemonade model download exceeded 30 minutes")
            time.sleep(10)
            jobs = self.request("/downloads")
            found = next((item for item in jobs if item.get("id") == job.get("id")), None)
            if found is None:
                models = self.request("/models")["data"]
                if any(m["id"] == MODEL and m.get("downloaded") for m in models):
                    break
                raise RuntimeError("Download job disappeared without an installed model")
            job = found
            print(f"Download: {job.get('percent', 0):.1f}%", flush=True)
        self.request(f"/models/{MODEL}/files")
        self.load()
        self.save_audit("lemonade-prepare.json")

    def load(self):
        self.request("/load", {"model_name": MODEL, "ctx_size": 4096,
                               "llamacpp_backend": "vulkan", "save_options": False,
                               "merge_args": False,
                               "llamacpp_args": "--parallel 1 --flash-attn on --batch-size 4096 --ubatch-size 1024"})
        return {"health": self.request("/health"), "system_info": self.request("/system-info"),
                "files": self.request(f"/models/{MODEL}/files")}

    def save_audit(self, filename):
        Path("/results", filename).write_text(json.dumps(self.audit, indent=2) + "\n")

    def _logprobs(self, prompt):
        start = time.perf_counter()
        tokens = self.request("/tokenize", {"model": MODEL, "content": prompt,
                                             "add_special": False, "parse_special": True})["tokens"]
        if len(tokens) >= 4096:
            raise ValueError("Decision prompt exceeds the evaluation context limit")
        response = self.request("/completions", {
            "model": MODEL, "prompt": tokens, "max_tokens": 1, "temperature": 0,
            "logprobs": self.top_k, "cache_prompt": False, "stream": False,
        })
        self.last_response = response
        assert response["model"] == MODEL, "Unexpected serving model"
        top = response["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
        seen = {entry["token"]: entry["logprob"] for entry in top}
        # Unlike the upstream top-k fallback, refuse an approximate distribution.
        assert all(letter in seen for letter in "ABC"), "An option is missing from returned logprobs"
        assert response["usage"]["prompt_tokens"] == len(tokens), "Prompt token count changed"
        assert response["timings"]["cache_n"] == 0, "Unexpected prefix-cache reuse"
        self.audit.clear()
        return seen, len(tokens), time.perf_counter() - start

    def system_one(self, state, questions, **_kwargs):
        answer = self.decide(state, questions["effect"])
        return {"answers": {"effect": answer},
                "usage": {"input_tokens": answer["input_tokens"], "output_tokens": 1},
                "transport": self.last_response}
