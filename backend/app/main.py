"""FastAPI 入口：AG-UI SSE 端点 + 人在环确认 + 编辑器回传 + 演示辅助接口。

启动：uv run uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import config
from app.agui import events as ag
from app.core import confirm, routes as routes_core, tasks as tasks_core
from app.core.store import STORE

app = FastAPI(title="无人机智能体后端", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Agent 运行（AG-UI SSE）───────────────────────────────────


class RunRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=2000)


@app.post("/api/agent/run")
async def agent_run(req: RunRequest) -> StreamingResponse:
    if config.AGENT_MODE == "llm":
        from app.agent.llm_runner import run_llm as runner
    else:
        from app.agent.scripted import run_scripted as runner

    async def gen():
        async for event in runner(req.thread_id, req.message):
            yield ag.sse(event)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 人在环确认（安全红线：高危操作人工点击后才签发一次性 token）──


@app.post("/api/confirmations/{action_id}/approve")
def approve_action(action_id: str) -> dict[str, Any]:
    r = confirm.approve(action_id)
    if r.get("error"):
        raise HTTPException(status_code=400, detail=r["error"])
    return r


@app.post("/api/confirmations/{action_id}/cancel")
def cancel_action(action_id: str) -> dict[str, Any]:
    r = confirm.cancel(action_id)
    if r.get("error"):
        raise HTTPException(status_code=404, detail=r["error"])
    return r


# ── 航线编辑器（免登录 iframe 专用，token 鉴权）──────────────


@app.get("/api/routes/{route_id}")
def get_route(route_id: str, token: str = Query(...)) -> dict[str, Any]:
    if not routes_core.validate_editor_token(route_id, token):
        raise HTTPException(status_code=401, detail="编辑链接已过期或无效，请让智能体重新打开编辑器")
    r = routes_core.get_route_detail(route_id)
    if r.get("error"):
        raise HTTPException(status_code=404, detail=r["error"])
    # 编辑器只显示该航线覆盖的图斑（不是全区图斑），避免远处图斑撑大视野
    from app.core import plots as plots_core

    covered_ids = [c["plot_id"] for c in r.get("covered_plots", [])]
    r["plots"] = [p for pid in covered_ids if (p := plots_core.get_plot(pid))]
    return r


class WaypointsUpdate(BaseModel):
    token: str
    waypoints: list[dict[str, float]]


@app.put("/api/routes/{route_id}/waypoints")
def put_waypoints(route_id: str, body: WaypointsUpdate) -> dict[str, Any]:
    if not routes_core.validate_editor_token(route_id, body.token):
        raise HTTPException(status_code=401, detail="编辑链接已过期或无效")
    if len(body.waypoints) < 2:
        raise HTTPException(status_code=422, detail="航点数量不足")
    r = routes_core.update_waypoints(route_id, body.waypoints)
    if r.get("error"):
        raise HTTPException(status_code=404, detail=r["error"])
    return r


# ── 飞行任务与演示辅助 ───────────────────────────────────────


@app.get("/api/tasks/{flight_task_id}")
def task_status(flight_task_id: str) -> dict[str, Any]:
    r = tasks_core.get_task_status(flight_task_id)
    if r.get("error"):
        raise HTTPException(status_code=404, detail=r["error"])
    return r


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    return {
        "agent_mode": config.AGENT_MODE,
        "model": config.LLM_MODEL if config.AGENT_MODE == "llm" else "scripted（关键词兜底路由）",
        "mcp_servers": ["drone-dispatch-mcp", "route-planning-mcp", "preflight-mcp", "flight-task-mcp"],
    }


@app.post("/api/reset")
def reset() -> dict[str, str]:
    """重新演示：重置世界状态与会话上下文。"""
    STORE.reset()
    from app.agent.scripted import reset_threads

    reset_threads()
    return {"status": "ok"}
