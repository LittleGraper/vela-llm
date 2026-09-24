"""Optional real Codex parser smoke test; no inference, user config or credentials."""

import asyncio
import json
import os
import shutil
import struct
import subprocess
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_model_context import catalog, model
from test_model_context import configured as configured

from client_config import ClientManager
from request_compatibility import normalize_request


@pytest.mark.skipif(not shutil.which("codex"), reason="Codex CLI is not installed")
async def test_codex_reads_vela_catalog(configured, tmp_path):
    settings, config = configured
    efforts = ["low", "medium", "high", "xhigh", "max"]
    catalog(
        config.parent,
        [
            {
                **model(),
                "capabilities": {"supports": {"reasoning_effort": efforts, "vision": True}},
            },
            model("model-b"),
        ],
    )
    manager = ClientManager(settings, home=tmp_path / "home", environ={})
    manager.apply(manager.plan("codex"))
    env = os.environ.copy()
    env.update(
        CODEX_HOME=str(manager.paths("codex")[0].parent),
        HOME=str(tmp_path / "home"),
        USERPROFILE=str(tmp_path / "home"),
    )
    with (tmp_path / "app-server.log").open("wb") as errors:
        process = await asyncio.create_subprocess_exec(
            shutil.which("codex"),
            "app-server",
            cwd=tmp_path,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=errors,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        async def call(request):
            process.stdin.write((json.dumps(request) + "\n").encode())
            await process.stdin.drain()
            async with asyncio.timeout(20):
                while line := await process.stdout.readline():
                    result = json.loads(line)
                    if result.get("id") == request["id"]:
                        assert "error" not in result
                        return result["result"]
            pytest.fail("Codex exited before replying")

        try:
            await call(
                {
                    "id": 1,
                    "method": "initialize",
                    "params": {"clientInfo": {"name": "vela-catalog-test", "version": "1.0"}},
                }
            )
            result = await call({"id": 2, "method": "model/list", "params": {}})
            assert {item["id"] for item in result["data"]} == {"model-a", "model-b"}
            reasoner = next(item for item in result["data"] if item["id"] == "model-a")
            assert [
                item["reasoningEffort"] for item in reasoner["supportedReasoningEfforts"]
            ] == efforts
            assert reasoner["defaultReasoningEffort"] == "medium"
            assert reasoner["inputModalities"] == ["text", "image"]
            text_only = next(item for item in result["data"] if item["id"] == "model-b")
            assert text_only["inputModalities"] == ["text"]
        finally:
            if process.returncode is None:
                process.terminate()
            await process.wait()


@pytest.mark.skipif(not shutil.which("codex"), reason="Codex CLI is not installed")
async def test_codex_image_tool_roundtrip_and_resumed_model_switch(configured, tmp_path):
    settings, config = configured
    entries = [
        {
            **model(name),
            "supported_endpoints": ["/responses"],
            "capabilities": {"supports": {"vision": True, "reasoning_effort": efforts}},
        }
        for name, efforts in [
            ("model-a", ["low", "medium", "high", "xhigh", "max"]),
            ("model-b", ["low", "medium", "high"]),
        ]
    ]
    catalog(config.parent, entries)
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            normalize_request(body, entries, "/responses")
            requests.append(body)
            index = len(requests)
            item = (
                {
                    "type": "function_call",
                    "id": "fc-1",
                    "call_id": "call-1",
                    "name": "update_plan",
                    "arguments": json.dumps(
                        {"plan": [{"step": "Check model configuration", "status": "completed"}]}
                    ),
                }
                if index == 1
                else {
                    "type": "message",
                    "id": f"msg-{index}",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "VELA_OK", "annotations": []}],
                }
            )
            response = {
                "id": f"resp-{index}",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": body["model"],
                "output": [item],
                "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
            }
            events = [
                {
                    "type": "response.created",
                    "response": {**response, "status": "in_progress", "output": []},
                },
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {"type": "response.completed", "response": response},
            ]
            payload = "".join(
                f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    settings.port = server.server_port
    manager = ClientManager(settings, home=tmp_path / "home", environ={})
    manager.apply(manager.plan("codex"))
    image = tmp_path / "pixel.png"

    def png_chunk(kind, data):
        return (
            struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))
        )

    image.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", struct.pack("!2I5B", 1, 1, 8, 2, 0, 0, 0))
        + png_chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + png_chunk(b"IEND", b"")
    )
    env = os.environ.copy()
    env.update(
        CODEX_HOME=str(manager.paths("codex")[0].parent),
        HOME=str(tmp_path / "home"),
        USERPROFILE=str(tmp_path / "home"),
    )

    async def run(*args):
        process = await asyncio.create_subprocess_exec(
            shutil.which("codex"),
            *args,
            cwd=tmp_path,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(45):
                stdout, stderr = await process.communicate()
            assert process.returncode == 0, stderr.decode(errors="replace")
            assert b"VELA_OK" in stdout, stdout.decode(errors="replace")
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()

    try:
        await run(
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "-c",
            'model_reasoning_effort="max"',
            "--model",
            "model-a",
            "--image",
            str(image),
            "--",
            "Inspect this image and say VELA_OK.",
        )
        assert len(requests) >= 2
        assert requests[0]["reasoning"]["effort"] == "max"
        assert any(
            part.get("type") == "input_image"
            for item in requests[0]["input"]
            for part in item.get("content", [])
            if isinstance(part, dict)
        ), requests[0]["input"]
        assert any(item.get("type") == "function_call_output" for item in requests[1]["input"])
        await run(
            "exec",
            "resume",
            "--last",
            "--json",
            "--skip-git-repo-check",
            "-c",
            'model_reasoning_effort="max"',
            "--model",
            "model-b",
            "Continue the previous conversation and say VELA_OK.",
        )
        assert requests[-1]["model"] == "model-b"
        assert requests[-1]["reasoning"]["effort"] == "medium"
        assert any(item.get("type") == "function_call_output" for item in requests[-1]["input"])
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
