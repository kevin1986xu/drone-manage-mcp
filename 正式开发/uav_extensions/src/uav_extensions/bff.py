"""GIS 态势前端 BFF：演示版 AG-UI 前端 ↔ DeerFlow Gateway 适配层。

演示版前端（分屏/地图/确认卡片/编辑器 iframe）零改动，把 /api 指到本服务即可：
  POST /api/agent/run {thread_id, message}   → AG-UI SSE（事件语义与演示版一致）
  POST /api/confirmations/{id}/approve|cancel → 代理审批服务（admin key 藏在服务端）
  GET  /api/routes/{id}?token= / PUT waypoints → 代理 route-planning 的编辑器 REST
  GET  /api/tasks/{id} / /api/config          → 兼容端点

事件映射（与演示版 llm_runner 的 astream_events→AG-UI 同构）：
  DeerFlow runs/stream(messages+values) → RUN_*/TEXT_MESSAGE_*/TOOL_CALL_*/VIEW_DIRECTIVE
  VIEW_DIRECTIVE 的几何回灌：LLM 拿瘦身结果，画图所需 geometry/waypoints 由 BFF
  经 MCP 工具（include_geometry/include_waypoints）补齐——BFF 自己就是 MCP 消费方。

运行：python -m uav_extensions.bff   # 默认 0.0.0.0:8300
环境：DEERFLOW_BASE(默认 :8001) DEERFLOW_ADMIN_TOKEN BFF_MODEL(默认 qwen3.7-plus)
      APPROVAL_BASE(默认 :8205) APPROVAL_ADMIN_KEY
      UAV_MCP_API_KEY  ROUTE_MCP_BASE(默认 :8202)
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, AsyncIterator

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DEERFLOW = os.getenv("DEERFLOW_BASE", "http://127.0.0.1:8001").rstrip("/")
DF_TOKEN = os.getenv("DEERFLOW_ADMIN_TOKEN", "").strip()
MODEL = os.getenv("BFF_MODEL", "qwen3.7-plus")
APPROVAL = os.getenv("APPROVAL_BASE", "http://127.0.0.1:8205").rstrip("/")
ADMIN_KEY = os.getenv("APPROVAL_ADMIN_KEY", "").strip()
API_KEY = os.getenv("UAV_MCP_API_KEY", "").strip()
ROUTE_MCP = os.getenv("ROUTE_MCP_BASE", "http://127.0.0.1:8202").rstrip("/")
DISPATCH_MCP = os.getenv("DISPATCH_MCP_BASE", "http://127.0.0.1:8201").rstrip("/")
TASK_MCP = os.getenv("TASK_MCP_BASE", "http://127.0.0.1:8204").rstrip("/")

app = FastAPI(title="UAV GIS BFF", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","),
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# 前端 thread_id（任意串）→ DeerFlow thread UUID
_threads: dict[str, str] = {}


def _df_headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if DF_TOKEN:
        h["Authorization"] = f"Bearer {DF_TOKEN}"
    return h


async def _df_thread(client: httpx.AsyncClient, frontend_tid: str) -> str:
    if frontend_tid not in _threads:
        r = await client.post(f"{DEERFLOW}/api/threads", json={}, headers=_df_headers())
        r.raise_for_status()
        _threads[frontend_tid] = r.json()["thread_id"]
    return _threads[frontend_tid]


# ── MCP 消费（几何回灌）─────────────────────────────────────


async def _mcp_call(base: str, tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """轻量 MCP tools/call（stateless server，单发 JSON-RPC 即可）。失败返回 None。"""
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        headers = {"X-API-Key": API_KEY} if API_KEY else {}
        async with streamablehttp_client(f"{base}/mcp", headers=headers) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                res = await s.call_tool(tool, args)
                return json.loads(res.content[0].text)
    except Exception as exc:  # noqa: BLE001 —— 回灌失败只降级视图，不断流
        logger.warning("几何回灌失败 %s: %s", tool, exc)
        return None


# ── 工具结果 → 右栏视图指令（自演示版 directives_for 平移）────

CHECK_TOOLS = {"check_weather", "check_battery", "check_route_obstacle", "check_drone_obstacle", "check_airspace"}


def _vd(directive: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": "VIEW_DIRECTIVE", "directive": directive, "payload": payload}


async def directives_for(tool: str, result: Any, call_args: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(result, dict) or result.get("error"):
        return []

    if result.get("status") == "requires_confirmation":
        out = []
        if result.get("action") == "create_task_plan" and result.get("schedule"):
            out.append(_vd("show_plan", {"schedule": result["schedule"], "feasible": result.get("feasible", True)}))
        out.append(_vd("show_confirm", {"action_id": result["action_id"], "action": result["action"],
                                        "summary": result["summary"]}))
        return out

    if result.get("status") == "plan_activated":
        return [_vd("show_plan", {"schedule": result.get("schedule", []),
                                  "plan_id": result.get("plan_id"), "active": True})]
    if tool == "get_plan_progress":
        return [_vd("show_plan", {"schedule": result.get("schedule", []),
                                  "plan_id": result.get("plan_id"), "active": True})]

    if tool == "query_plots":
        plots = result.get("plots", [])
        if plots and "geometry" not in plots[0]:  # 瘦身结果 → 带几何重查（TTL 缓存内，秒回）
            full = await _mcp_call(DISPATCH_MCP, "query_plots", {**call_args, "include_geometry": True})
            if full and full.get("plots"):
                plots = full["plots"]
        return [_vd("show_map", {"layer": "plots", "plots": plots})]

    if tool == "find_nearby_drones":
        return [_vd("show_map", {"layer": "drones", "drones": result.get("drones", [])})]

    if tool in {"generate_route", "get_route_detail"}:
        geometry = result.get("geometry")
        if not geometry:
            detail = await _mcp_call(ROUTE_MCP, "get_route_detail",
                                     {"route_id": result["route_id"], "include_waypoints": True})
            if detail:
                geometry = detail.get("geometry")
        return [_vd("show_map", {"layer": "route", "route": {
            "route_id": result["route_id"],
            "version": result.get("version", 1),
            "length_km": result["length_km"],
            "duration_min": result["duration_min"],
            "geometry": geometry,
            "covered_plots": result["covered_plots"],
        }})]

    if tool == "explain_route":
        covered = [c["plot_id"] for c in result.get("decision", {}).get("covered_plots", [])]
        return [_vd("show_map", {"layer": "highlight", "plot_ids": covered})]

    if tool == "open_route_editor":
        return [_vd("show_iframe", {"url": result["url"], "route_id": result["route_id"]})]

    if tool in CHECK_TOOLS:
        return [_vd("show_report", {"mode": "append", "check": result})]

    if tool == "preflight_check":
        return [_vd("show_report", {"mode": "full", "checks": result.get("checks", []),
                                    "overall": result.get("overall")})]

    if tool == "take_off" and result.get("status") == "airborne":
        return [_vd("show_map", {"layer": "flight", "task": {
            "flight_task_id": result["flight_task_id"],
            "drone_id": result["drone_id"],
            "route_id": result["route_id"],
            "duration_min": result["duration_min"],
        }})]

    return []


# ── DeerFlow SSE → AG-UI SSE ────────────────────────────────


def _strip(name: str) -> str:
    return name.split("-mcp_", 1)[1] if "-mcp_" in name else name


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


class RunRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=2000)


async def _agui_stream(frontend_tid: str, message: str) -> AsyncIterator[str]:
    run_id = uuid.uuid4().hex[:8]
    yield _sse({"type": "RUN_STARTED", "run_id": run_id})
    started_calls: set[str] = set()          # 已发 TOOL_CALL_START 的 id
    call_args: dict[str, str] = {}           # tool_call_id → args JSON 串（chunk 累积）
    call_args_full: dict[str, dict] = {}     # tool_call_id → 完整 args（tool_calls 直带，chunk 缺失时兜底）
    chunk_cid: dict[int, str] = {}           # chunk index → tool_call_id（续段 id=None，靠 index 关联）
    call_names: dict[str, str] = {}
    text_open: str | None = None
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            df_tid = await _df_thread(client, frontend_tid)
            async with client.stream(
                "POST", f"{DEERFLOW}/api/threads/{df_tid}/runs/stream",
                headers={**_df_headers(), "Accept": "text/event-stream"},
                json={
                    "input": {"messages": [{"type": "human", "content": message}]},
                    "context": {"model_name": MODEL},
                    "stream_mode": ["messages"],
                },
            ) as resp:
                resp.raise_for_status()
                event_name = None
                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    if event_name not in (None, "messages"):
                        continue
                    try:
                        payload = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if not (isinstance(payload, list) and payload):
                        continue
                    msg = payload[0]
                    mtype = str(msg.get("type", ""))

                    # 助手文本增量
                    if mtype.startswith("AIMessageChunk") or mtype == "ai":
                        content = msg.get("content")
                        if isinstance(content, str) and content:
                            mid = msg.get("id") or "m0"
                            if text_open != mid:
                                if text_open:
                                    yield _sse({"type": "TEXT_MESSAGE_END", "message_id": text_open})
                                text_open = mid
                                yield _sse({"type": "TEXT_MESSAGE_START", "message_id": mid})
                            yield _sse({"type": "TEXT_MESSAGE_CONTENT", "message_id": mid, "delta": content})
                        # 工具调用启动（chunk 带 name 时发 START；args 分片累积）
                        for tc in (msg.get("tool_calls") or []):
                            cid = tc.get("id")
                            if cid and isinstance(tc.get("args"), dict) and tc["args"]:
                                call_args_full[cid] = tc["args"]
                            if cid and tc.get("name") and cid not in started_calls:
                                started_calls.add(cid)
                                call_names[cid] = _strip(tc["name"])
                                yield _sse({"type": "TOOL_CALL_START", "tool_call_id": cid,
                                            "tool_name": call_names[cid], "args": tc.get("args") or {}})
                        for ch in (msg.get("tool_call_chunks") or []):
                            idx = ch.get("index")
                            if ch.get("id") and idx is not None:
                                chunk_cid[idx] = ch["id"]
                            cid = ch.get("id") or (chunk_cid.get(idx) if idx is not None else None)
                            if cid:
                                call_args[cid] = call_args.get(cid, "") + (ch.get("args") or "")

                    # 工具结果
                    elif mtype == "tool":
                        cid = msg.get("tool_call_id") or ""
                        tool = _strip(msg.get("name") or "")
                        raw = msg.get("content")
                        if isinstance(raw, list):
                            raw = "".join(x.get("text", "") for x in raw if isinstance(x, dict))
                        try:
                            result = json.loads(raw) if isinstance(raw, str) else raw
                        except ValueError:
                            result = {"text": raw}
                        yield _sse({"type": "TOOL_CALL_END", "tool_call_id": cid,
                                    "tool_name": tool, "result": result})
                        try:
                            args = json.loads(call_args.get(cid) or "{}")
                        except ValueError:
                            args = {}
                        if not args:
                            args = call_args_full.get(cid, {})
                        for d in await directives_for(tool, result, args if isinstance(args, dict) else {}):
                            yield _sse(d)
        if text_open:
            yield _sse({"type": "TEXT_MESSAGE_END", "message_id": text_open})
        yield _sse({"type": "RUN_FINISHED", "run_id": run_id})
    except Exception as exc:  # noqa: BLE001
        logger.exception("run 失败")
        yield _sse({"type": "RUN_ERROR", "message": str(exc)})


@app.post("/api/agent/run")
async def agent_run(req: RunRequest) -> StreamingResponse:
    return StreamingResponse(
        _agui_stream(req.thread_id, req.message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 人在环确认（代理审批服务；admin key 不出服务端）──────────


@app.post("/api/confirmations/{action_id}/approve")
async def approve_action(action_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"{APPROVAL}/api/approval/{action_id}/approve",
                         headers={"X-Admin-Key": ADMIN_KEY} if ADMIN_KEY else {})
    if r.status_code != 200:
        raise HTTPException(status_code=400, detail=(r.json() or {}).get("detail", "确认失败"))
    return r.json()


@app.post("/api/confirmations/{action_id}/cancel")
async def cancel_action(action_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"{APPROVAL}/api/approval/{action_id}/cancel",
                         headers={"X-Admin-Key": ADMIN_KEY} if ADMIN_KEY else {})
    if r.status_code != 200:
        raise HTTPException(status_code=404, detail="确认单不存在")
    return r.json()


# ── 航线编辑器代理（REST 在 route-planning 服务上，进程内直达状态）──


@app.get("/api/routes/{route_id}")
async def get_route(route_id: str, token: str = Query(...)) -> Any:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{ROUTE_MCP}/api/routes/{route_id}", params={"token": token},
                        headers={"X-API-Key": API_KEY} if API_KEY else {})
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text[:200])
    return r.json()


@app.put("/api/routes/{route_id}/waypoints")
async def put_waypoints(route_id: str, body: dict[str, Any]) -> Any:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.put(f"{ROUTE_MCP}/api/routes/{route_id}/waypoints", json=body,
                        headers={"X-API-Key": API_KEY} if API_KEY else {})
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text[:200])
    return r.json()


# ── 兼容端点 ────────────────────────────────────────────────


@app.get("/api/tasks/{flight_task_id}")
async def task_status(flight_task_id: str) -> Any:
    r = await _mcp_call(TASK_MCP, "get_task_status", {"flight_task_id": flight_task_id})
    if not r or r.get("error"):
        raise HTTPException(status_code=404, detail=(r or {}).get("error", "任务不存在"))
    return r


@app.get("/api/config")
async def get_config() -> dict[str, Any]:
    return {
        "agent_mode": "deerflow",
        "model": MODEL,
        "mcp_servers": ["uav-drone-dispatch-mcp", "uav-route-planning-mcp",
                        "uav-preflight-mcp", "uav-flight-task-mcp"],
    }


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    uvicorn.run(app, host=os.getenv("BFF_HOST", "0.0.0.0"),
                port=int(os.getenv("BFF_PORT", "8300")), log_level="info")
