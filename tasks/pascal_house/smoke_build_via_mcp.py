#!/usr/bin/env python3
"""ANSWER-KEY-SIDE HARNESS — never give this to a blind run.

Drives the real `@pascal-app/mcp` server over stdio and builds PH-1 exactly
from the reference layout, then exports the scene. Purpose: end-to-end
pipeline validation (server launch -> semantic tools -> export_json ->
grader) against the actual published tool surface, and yaw/units calibration.
A perfect realization should grade ~0 mm / 100% / all gates PASS; any
systematic offset it reveals is a real contract mismatch to fix in the
grader or kit before benchmark runs.

Usage:
  py -3.11 tasks/pascal_house/smoke_build_via_mcp.py \
      --ref tasks/pascal_house/ph1_reference_layout.yaml \
      --out <workdir>  [--launch "bunx @pascal-app/mcp"]

Writes <workdir>/run_scene.json plus a transcript of tool results.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import yaml


class McpStdioClient:
    """Minimal MCP client: newline-delimited JSON-RPC over a child's stdio."""

    def __init__(self, cmd: list[str], env: dict, cwd: Path):
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd),
            env=env,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._id = 0
        self._stderr_lines: list[str] = []
        t = threading.Thread(target=self._drain_stderr, daemon=True)
        t.start()

    def _drain_stderr(self):
        for line in self.proc.stderr:
            self._stderr_lines.append(line.rstrip())

    def _send(self, msg: dict):
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def _read_response(self, want_id: int, timeout: float = 120.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError(
                    "server closed stdout; stderr tail:\n" + "\n".join(self._stderr_lines[-15:])
                )
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == want_id:
                return msg
            # else: notification or unrelated message; keep reading
        raise TimeoutError(f"no response to request {want_id}")

    def request(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})
        msg = self._read_response(self._id)
        if "error" in msg:
            raise RuntimeError(f"{method} -> {json.dumps(msg['error'])}")
        return msg.get("result", {})

    def notify(self, method: str, params: dict | None = None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def call_tool(self, name: str, arguments: dict) -> dict:
        res = self.request("tools/call", {"name": name, "arguments": arguments})
        if res.get("isError"):
            raise RuntimeError(f"tool {name} error: {json.dumps(res)[:800]}")
        if isinstance(res.get("structuredContent"), dict):
            return res["structuredContent"]
        for block in res.get("content", []):
            if block.get("type") == "text":
                try:
                    return json.loads(block["text"])
                except json.JSONDecodeError:
                    return {"text": block["text"]}
        return res

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def wall_t(wall: dict, center: list[float]) -> float:
    (sx, sz), (ex, ez) = wall["start"], wall["end"]
    length = math.hypot(ex - sx, ez - sz)
    return ((center[0] - sx) * (ex - sx) + (center[1] - sz) * (ez - sz)) / (length * length)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--launch", default="bunx @pascal-app/mcp")
    args = ap.parse_args()

    ref = yaml.safe_load(args.ref.read_text(encoding="utf-8"))
    args.out.mkdir(parents=True, exist_ok=True)
    transcript = []

    def log(step, payload):
        transcript.append({"step": step, "result": payload})
        print(f"  {step}: {json.dumps(payload)[:160]}")

    env = dict(os.environ)
    env["PASCAL_DATA_DIR"] = str(args.out / "pascal-data")

    print(f"launching: {args.launch}")
    client = McpStdioClient(args.launch.split(), env=env, cwd=args.out)
    try:
        init = client.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "marb-a-smoke", "version": "0.1"},
            },
        )
        client.notify("notifications/initialized")
        print(f"server: {init.get('serverInfo')}")

        tools = client.request("tools/list").get("tools", [])
        print(f"tools exposed: {len(tools)}")

        scene = client.call_tool("get_scene", {})
        nodes = scene.get("nodes") or json.loads(scene.get("json", "{}")).get("nodes", {})
        if isinstance(nodes, dict):
            node_list = list(nodes.values())
        else:
            node_list = nodes
        level_id = next(n["id"] for n in node_list if n.get("type") == "level")
        print(f"default level: {level_id}")

        t0 = time.time()
        env_cfg = ref["envelope"]
        wall_ids: dict[str, str] = {}
        for w in ref["walls"]:
            res = client.call_tool(
                "create_wall",
                {
                    "levelId": level_id,
                    "start": list(w["start"]),
                    "end": list(w["end"]),
                    "thickness": env_cfg["wall_thickness"],
                    "height": env_cfg["wall_height"],
                },
            )
            wall_ids[w["id"]] = res["wallId"]
            log(f"create_wall {w['id']}", res)

        ref_walls = {w["id"]: w for w in ref["walls"]}
        for d in ref["doors"]:
            res = client.call_tool(
                "add_door",
                {
                    "wallId": wall_ids[d["wall"]],
                    "t": wall_t(ref_walls[d["wall"]], d["center"]),
                    "width": 0.9,
                    "height": 2.1,
                },
            )
            log(f"add_door {d['id']}", res)

        for w in ref["windows"]:
            res = client.call_tool(
                "add_window",
                {
                    "wallId": wall_ids[w["wall"]],
                    "t": wall_t(ref_walls[w["wall"]], w["center"]),
                    "width": 1.2,
                    "height": 1.2,
                    "sillHeight": 0.9,
                },
            )
            log(f"add_window {w['id']}", res)

        for i, it in enumerate(ref["items"]):
            res = client.call_tool(
                "place_item",
                {
                    "catalogItemId": it["asset"],
                    "targetNodeId": level_id,
                    "position": [it["center"][0], 0, it["center"][1]],
                    "rotation": math.radians(it["yaw_deg"]),
                },
            )
            log(f"place_item {i} {it['asset']}", res)

        checks = {}
        for tool in ("check_collisions", "validate_scene", "verify_scene"):
            try:
                checks[tool] = client.call_tool(tool, {})
            except RuntimeError as exc:  # non-fatal for the smoke run
                checks[tool] = {"error": str(exc)[:400]}
            log(tool, checks[tool])

        exported = client.call_tool("export_json", {"pretty": True})
        scene_json = exported["json"] if "json" in exported else json.dumps(exported)
        out_scene = args.out / "run_scene.json"
        out_scene.write_text(scene_json, encoding="utf-8")
        elapsed = time.time() - t0
        (args.out / "smoke_transcript.json").write_text(
            json.dumps({"elapsed_s": round(elapsed, 1), "steps": transcript}, indent=2),
            encoding="utf-8",
        )
        print(f"\nwrote {out_scene} ({len(scene_json)} bytes) in {elapsed:.1f}s")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
