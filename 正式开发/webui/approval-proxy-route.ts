/**
 * UAV 审批服务的同源服务端代理（Next.js Route Handler）。
 *
 * 浏览器只打同源 POST /api/uav/approval，body: {action_id, verb}；
 * X-Admin-Key 取自服务端 env（UAV_APPROVAL_ADMIN_KEY），不下发浏览器。
 *
 * 用静态路由 + body 传参而非 /[actionId]/[verb] 动态段：该 DeerFlow 版本
 * （Next 16 + i18n 配置 + --webpack dev）下动态段 API 路由不注册，
 * 请求会漏进 next.config 的 /api/:path* catch-all rewrite 打到 Gateway。
 *
 * 本文件真身在 正式开发/webui/，由 install.sh 拷贝为
 * deerflow/frontend/src/app/api/uav/approval/route.ts
 * （route 文件必须实拷贝：App Router 路由扫描不收指向项目外的软链）。
 */

import type { NextRequest } from "next/server";

const APPROVAL_BASE =
  process.env.UAV_APPROVAL_BASE ?? "http://127.0.0.1:8205";
const ADMIN_KEY = process.env.UAV_APPROVAL_ADMIN_KEY ?? "";

export async function POST(request: NextRequest) {
  const body = (await request.json().catch(() => ({}))) as {
    action_id?: string;
    verb?: string;
  };
  const { action_id, verb } = body;
  if (!action_id || (verb !== "approve" && verb !== "cancel")) {
    return Response.json(
      { detail: "参数错误：需要 action_id 与 verb(approve|cancel)" },
      { status: 422 },
    );
  }
  const response = await fetch(
    `${APPROVAL_BASE}/api/approval/${encodeURIComponent(action_id)}/${verb}`,
    {
      method: "POST",
      headers: ADMIN_KEY ? { "X-Admin-Key": ADMIN_KEY } : {},
    },
  );
  return new Response(await response.arrayBuffer(), {
    status: response.status,
    headers: { "Content-Type": "application/json" },
  });
}
