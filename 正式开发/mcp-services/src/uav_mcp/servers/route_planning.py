"""uav-route-planning-mcp：航线域（规划/详情/决策解释/编辑器）。

除 MCP 工具外，还挂两条编辑器 REST（custom_route，与 /mcp 同端口）：
  GET /api/routes/{id}?token=   编辑器取数（航点+覆盖图斑几何，editor token 鉴权）
  PUT /api/routes/{id}/waypoints 编辑器保存（生成新版本并回写平台）
供 BFF 代理；不是 Agent 工具。
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from uav_mcp import plots as plots_core
from uav_mcp import routes as routes_core
from uav_mcp.servers import as_list


def build() -> FastMCP:
    mcp = FastMCP(
        "uav-route-planning-mcp",
        instructions="无人机航线域：图斑核查航线生成（平台图斑巡检算法+多图斑合并决策）、决策解释、人工编辑",
    )

    @mcp.tool()
    def generate_route(
        drone_id: str,
        plot_ids: list[str] | str,
        strategy: str = "multi_cover",
        altitude_m: float = 120.0,
        overlap_rate: float = 0.7,
        photo_num: int = 4,
        replace_route_id: str | None = None,
    ) -> dict[str, Any]:
        """生成/重规划覆盖指定图斑的核查航线（也是自然语言优化航线的工具）。

        plot_ids 为用户指定的目标图斑（必须生效）；multi_cover 自动合并同航向带邻近图斑。

        ── 自然语言软约束优化（把用户诉求翻译成参数，重新调用本工具，
           并传 replace_route_id=当前航线 route_id 以替换旧航线并拿到前后对比）──
          · "飞低一点/降低高度"        → altitude_m（平台安全带约 100~120m，低于下限会被顶回）
          · "多拍几张/拍密一点/精度高些" → photo_num（每图斑拍照点数，最有效的清晰度杠杆）
          · "只飞这一块/别顺带其他图斑"  → strategy="single" 或收窄 plot_ids
          · "把旁边那块也加进来/去掉某块" → 调整 plot_ids
        这些都是整条航线统一的参数（不支持逐图斑差异化）。

        返回 feasibility（续航预算校验）：within_budget=false 时按 hint 放宽参数
        （降 photo_num / 减图斑 / 换高电量设备）后重规划，别硬报可行。
        change_vs_previous 给出与被替换航线的前后对比，用它向用户复述变化。
        平台算法做不到的（"避开村庄上空"等）如实说明，引导 open_route_editor 手动调整。
        """
        return routes_core.generate_route(
            drone_id, as_list(plot_ids), strategy, altitude_m, overlap_rate, photo_num, replace_route_id
        )

    @mcp.tool()
    def get_route_detail(route_id: str, version: int | None = None, include_waypoints: bool = False) -> dict[str, Any]:
        """查询航线的最新详情：航程、时长、覆盖图斑、与上一版本 diff。

        只要用户询问航线的**事实数据**——"航线多长"、"要飞多久"、"航点列表给我看看"、
        "现在这条航线什么情况"——都必须调用本工具取最新版本再回答；
        注意：追问**规划原因**（"为什么这么规划""为啥覆盖这么多图斑"）用 explain_route，不是本工具。
        航线可能已被人工编辑更新，禁止引用对话历史里的旧数据。
        编辑器回传（[EDITOR_SAVED]）后也用本工具，并根据 diff_vs_prev 复述变更影响。
        默认不含航点坐标（省上下文）；GIS 展示需要时传 include_waypoints=true。
        """
        return routes_core.get_route_detail(route_id, version, include_waypoints)

    @mcp.tool()
    def explain_route(route_id: str) -> dict[str, Any]:
        """返回航线规划算法的结构化决策依据，用于向用户解释"为什么这么规划"。

        用户**每次**追问规划原因都必须重新调用本工具——"为什么这么规划"、
        "这条航线是怎么考虑的"、"为啥要覆盖这么多图斑"、"解释一下航线逻辑"——
        即使你之前已经解释过，也要重新调用取最新决策数据（航线可能已变更），
        禁止凭对话记忆复述。
        返回：合并原因/放弃原因/架次对比/续航预算。转述时不得编造数据之外的理由。
        """
        return routes_core.explain_route(route_id)

    @mcp.tool()
    def open_route_editor(route_id: str) -> dict[str, Any]:
        """生成航线编辑界面免登录链接（临时 token，10 分钟有效）。仅对已生成航线的图斑可用。"""
        return routes_core.open_route_editor(route_id)

    # ── 编辑器 REST（editor token 鉴权，非 Agent 工具）────────

    @mcp.custom_route("/api/routes/{route_id}", methods=["GET"])
    async def editor_get(request: Request) -> JSONResponse:
        route_id = request.path_params["route_id"]
        token = request.query_params.get("token", "")
        if not routes_core.validate_editor_token(route_id, token):
            return JSONResponse({"detail": "编辑链接已过期或无效，请让智能体重新打开编辑器"}, status_code=401)
        r = routes_core.get_route_detail(route_id, include_waypoints=True)
        if r.get("error"):
            return JSONResponse({"detail": r["error"]}, status_code=404)
        # 只带该航线覆盖的图斑（避免远处图斑撑大编辑器视野）
        covered_ids = [c["plot_id"] for c in r.get("covered_plots", [])]
        r["plots"] = [p for pid in covered_ids if (p := plots_core.get_plot(pid, include_geometry=True))]
        return JSONResponse(r)

    @mcp.custom_route("/api/routes/{route_id}/waypoints", methods=["PUT"])
    async def editor_put(request: Request) -> JSONResponse:
        route_id = request.path_params["route_id"]
        body = await request.json()
        if not routes_core.validate_editor_token(route_id, body.get("token", "")):
            return JSONResponse({"detail": "编辑链接已过期或无效"}, status_code=401)
        waypoints = body.get("waypoints") or []
        if len(waypoints) < 2:
            return JSONResponse({"detail": "航点数量不足"}, status_code=422)
        if not all(isinstance(w, dict) and isinstance(w.get("lon"), (int, float))
                   and isinstance(w.get("lat"), (int, float)) for w in waypoints):
            return JSONResponse({"detail": "航点格式错误：应为 {lon, lat} 对象数组"}, status_code=422)
        r = routes_core.update_waypoints(route_id, waypoints)
        if r.get("error"):
            return JSONResponse({"detail": r["error"]}, status_code=404)
        return JSONResponse(r)

    return mcp
