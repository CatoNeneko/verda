"""Black-box HTTP acceptance for the vdra Alpha wheel.

All state, ports, identity and embedding traffic are synthetic and local to this
test. Run with ``python -m unittest -v tests.test_http_acceptance`` in the
release-wheel environment.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _EmbeddingHandler(BaseHTTPRequestHandler):
    """Tiny deterministic OpenAI-compatible embedding fixture; no model call."""

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path.rstrip("/") != "/v1/embeddings":
            self.send_error(404)
            return
        time.sleep(getattr(self.server, "delay", 0))
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        texts = payload.get("input", [])
        if isinstance(texts, str):
            texts = [texts]
        rows = []
        for index, text in enumerate(texts):
            digest = hashlib.sha256(str(text).encode("utf-8")).digest()
            vector = [((digest[i] / 127.5) - 1.0) for i in range(8)]
            rows.append({"object": "embedding", "index": index, "embedding": vector})
        body = json.dumps(
            {"object": "list", "data": rows, "model": "vdra-fixture"}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args) -> None:
        pass


class HttpAcceptanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.opener = build_opener(ProxyHandler({}))
        self.temp = tempfile.TemporaryDirectory(prefix="vdra-http-")
        self.root = Path(self.temp.name)
        self.port = _free_port()
        self.embed_port = _free_port()
        self.embed_server = ThreadingHTTPServer(
            ("127.0.0.1", self.embed_port), _EmbeddingHandler
        )
        self.embed_thread = Thread(target=self.embed_server.serve_forever, daemon=True)
        self.embed_thread.start()

        self.work_memory = {
            "enabled": True,
            "account": "alpha-test",
            "user": "tester",
            "projects": {
                "garden-study": {
                    "aliases": ["Garden Study"],
                    "facts_uri": "viking://user/tester/memories/entities/projects/garden-study.md",
                }
            },
            "session_projects": {},
        }
        (self.root / "work-memory.json").write_text(
            json.dumps(self.work_memory), encoding="utf-8"
        )
        config = {
            "embedding": {
                "dense": {
                    "provider": "openai",
                    "model": "vdra-fixture",
                    "api_base": f"http://127.0.0.1:{self.embed_port}/v1",
                    "api_key": "fixture-only",
                    "dimension": 8,
                    "encoding_format": "float",
                }
            },
            "storage": {"workspace": str(self.root / "workspace")},
            "server": {"host": "127.0.0.1", "port": self.port, "auth_mode": "dev"},
        }
        self.config_path = self.root / "ov.conf"
        self.config_path.write_text(json.dumps(config), encoding="utf-8")
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.headers = {
            "X-OpenViking-Account": "alpha-test",
            "X-OpenViking-User": "tester",
        }
        try:
            self._start_server()
        except Exception:
            self._stop_process()
            self._close_fixture()
            raise

    def _start_server(self, use_vdra=True) -> None:
        self.log_path = self.root / "server.log"
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("OPENVIKING_") and not key.lower().endswith("_proxy")
        }
        env.update(
            PYTHONUNBUFFERED="1",
            OPENVIKING_CLI_CONFIG_FILE=str(self.root / "ovcli.conf"),
        )
        if use_vdra:
            command = [sys.executable, "-m", "vdra", "serve", "--home", str(self.root)]
        else:
            command = [
                sys.executable,
                "-m",
                "openviking_cli.server_bootstrap",
                "--workers",
                "1",
            ]
        with self.log_path.open("wb") as log:
            self.proc = subprocess.Popen(
                [*command, "--config", str(self.config_path)],
                cwd=self.root,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
            )
        self._wait_ready()

    def tearDown(self) -> None:
        self._stop_process()
        self._close_fixture()

    def _close_fixture(self) -> None:
        self.embed_server.shutdown()
        self.embed_server.server_close()
        self.embed_thread.join(timeout=2)
        self.temp.cleanup()

    def _stop_process(self) -> None:
        if getattr(self, "proc", None) is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)

    def _request(
        self, method: str, path: str, payload: dict | None = None
    ) -> tuple[int, dict, dict]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = dict(self.headers)
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(
            self.base_url + path, data=data, headers=headers, method=method
        )
        try:
            response = self.opener.open(request, timeout=20)
        except HTTPError as error:
            raw = error.read()
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                body = {"raw": raw.decode("utf-8", "replace")}
            return error.code, body, dict(error.headers.items())
        with response:
            raw = response.read()
            return (
                response.status,
                json.loads(raw or b"{}"),
                dict(response.headers.items()),
            )

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + 60
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                self.fail(
                    self.log_path.read_text(encoding="utf-8", errors="replace")[-6000:]
                )
            try:
                status, body, _ = self._request("GET", "/health")
                if status == 200 and body.get("healthy") is True:
                    return
            except (OSError, URLError, ValueError) as exc:
                last_error = exc
            time.sleep(0.25)
        self.fail(f"vdra server did not become ready; last error: {last_error!r}")

    def test_source_write_cross_session_recall_and_missing_evidence(self) -> None:
        from vdra.fact_evidence import digest, source_text
        from vdra.vdra_state import atomic_json
        from vdra.work_focus import add_edge

        status, health, _ = self._request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(health.get("healthy"))
        self.assertIn("version", health)

        # A single native source write creates synthetic project evidence. The
        # identity is deliberately non-Codex and confined to this temp volume.
        facts = (
            "# Garden Study project facts\n"
            "Updated: 2026-11-19\n"
            "Dataset split: train 70%, validation 15%, test 15%.\n"
            "Current decision: use the fixed held-out test set for the next seedling count.\n"
        )
        facts_uri = self.work_memory["projects"]["garden-study"]["facts_uri"]
        status, made, _ = self._request(
            "POST", "/api/v1/fs/mkdir", {"uri": facts_uri.rsplit("/", 1)[0]}
        )
        self.assertEqual(status, 200, made)
        status, written, _ = self._request(
            "POST",
            "/api/v1/content/batch-write",
            {
                "root_uri": "viking://user/tester/memories/entities/projects",
                "operations": [{"uri": facts_uri, "content": facts, "mode": "upsert"}],
                "defer_processing": True,
                "wait": True,
            },
        )
        self.assertEqual(status, 200, written)
        self.assertEqual(written.get("status"), "ok")

        # Indexing is an explicit follow-up, so source write itself has no
        # background semantic/vector generation side effect.
        status, indexed, _ = self._request(
            "POST",
            "/api/v1/content/reindex",
            {
                "uri": facts_uri,
                "mode": "vectors_only",
                "wait": True,
                "recursive": False,
            },
        )
        self.assertEqual(status, 200, indexed)

        def recall(query, session_id):
            status, body, _ = self._request(
                "POST",
                "/api/v1/search/search",
                {
                    "mode": "context",
                    "session_id": session_id,
                    "query": query,
                    "query_expansion": "off",
                    "rewrite": False,
                    "max_tokens": 512,
                },
            )
            self.assertEqual(status, 200, body)
            self.assertEqual(body["status"], "ok", body)
            return body["result"]

        empty = recall("Garden Study dataset split", "codex-alpha-one")
        self.assertEqual(empty["stats"]["recall_outcome"]["status"], "empty_policy")

        # Search from another session proves project facts are available across
        # sessions. Disabling expansion/rewrite keeps this synthetic probe stable.
        query = "What dataset split did we decide for Garden Study?"
        result = recall(query, "dsh-alpha-two")
        stats = result["stats"]
        self.assertEqual(stats["recall_outcome"]["adapter_revision"], "vdra-0.1.0a2")
        self.assertIn("70%", result["rendered"])

        gap = recall(
            "Verify the Garden Study dataset split; what preregistered sensor protocol did we decide?",
            "dsh-alpha-two",
        )
        self.assertTrue(gap["stats"]["fact_coverage"]["missing"], gap["stats"])

        # Keep this source unindexed so only the explicit relation can find it.
        linked_dir = facts_uri.removesuffix(".md")
        linked_uri = linked_dir + "/seed.md"
        quote = "The dataset split decision depends on this recorded random seed."
        linked_text = f"# Garden Study protocol\nRandom seed: 5712.\n{quote}\n"
        code, made, _ = self._request("POST", "/api/v1/fs/mkdir", {"uri": linked_dir})
        self.assertEqual(code, 200, made)
        code, saved, _ = self._request(
            "POST",
            "/api/v1/content/batch-write",
            {
                "root_uri": linked_dir,
                "operations": [{"uri": linked_uri, "content": linked_text, "mode": "upsert"}],
                "defer_processing": True,
                "wait": True,
            },
        )
        self.assertEqual(code, 200, saved)
        self.assertEqual(saved["status"], "ok")
        query_with_gap = "For Garden Study, list the dataset split and the random seed."
        without_edge = recall(query_with_gap, "dsh-alpha-no-relation")
        self.assertTrue(without_edge["stats"]["fact_coverage"]["missing"])
        self.assertNotIn("5712", without_edge["rendered"])

        state_path = self.root / "work-state.json"
        states = json.loads(state_path.read_text(encoding="utf-8"))
        linked_hash = digest(source_text(linked_text))
        states["dsh-alpha-edge-owner"] = add_edge(
            {"project": "garden-study"},
            source_uri=facts_uri,
            target_uri=linked_uri,
            relation="depends_on",
            source_hash=digest(source_text(facts)),
            target_hash=linked_hash,
            evidence_uri=linked_uri,
            evidence_hash=linked_hash,
            evidence_quote=quote,
            evidence_date="2026-11-19",
            evidence_status="current",
            status="current",
        )
        atomic_json(state_path, states)
        linked = recall(query_with_gap, "dsh-alpha-with-relation")
        self.assertEqual(linked["stats"]["explicit_relations"]["accepted"], 1)
        self.assertEqual(linked["stats"]["explicit_relations"]["hops"], 1)
        self.assertEqual(linked["stats"]["fact_coverage"]["missing"], [])
        self.assertIn("5712", linked["rendered"])

        asyncio.run(self._check_mcp(query))

        # A slow vector backend exercises the server-side deadline.
        self.work_memory["context_deadline_seconds"] = 0.35
        (self.root / "work-memory.json").write_text(
            json.dumps(self.work_memory), encoding="utf-8"
        )
        self.embed_server.delay = 2
        timed = recall(
            "What preregistered spectroscopy protocol did we decide for Garden Study?",
            "dsh-alpha-timeout",
        )
        self.assertIn(
            timed["stats"]["recall_outcome"]["status"],
            {"server_deadline", "degraded_timeout"},
        )
        self.embed_server.delay = 0
        del self.work_memory["context_deadline_seconds"]
        (self.root / "work-memory.json").write_text(
            json.dumps(self.work_memory), encoding="utf-8"
        )

        # Roll back to upstream and start the adapter again, keeping test data.
        self._stop_process()
        self._start_server(use_vdra=False)
        code, data, _ = self._request(
            "GET", "/api/v1/content/read?" + urlencode({"uri": facts_uri})
        )
        self.assertEqual(code, 200, data)
        self.assertIn("70%", json.dumps(data))
        self._stop_process()
        self._start_server()
        after = recall(query, "dsh-alpha-after-restart")
        self.assertIn("70%", after["rendered"])
        states = json.loads((self.root / "work-state.json").read_text(encoding="utf-8"))
        self.assertIn("dsh-alpha-two", states)
        self.assertIn("dsh-alpha-after-restart", states)

    async def _check_mcp(self, query):
        from httpx import AsyncClient
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async with AsyncClient(headers=self.headers, trust_env=False) as client:
            async with streamable_http_client(
                self.base_url + "/mcp", http_client=client
            ) as (reader, writer, _):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertIn("search", {tool.name for tool in tools.tools})
                    result = await session.call_tool(
                        "search",
                        {
                            "mode": "context",
                            "query": query,
                            "session_id": "mcp-alpha-three",
                            "rewrite": "off",
                        },
                    )
                    self.assertFalse(result.isError, result)
                    self.assertIn("70%", result.model_dump_json())


if __name__ == "__main__":
    unittest.main()
