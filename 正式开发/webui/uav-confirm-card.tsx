"use client";

/**
 * UAV 高危操作确认卡片（DeerFlow Web UI 原生 HITL）。
 *
 * MCP 工具（take_off/dispatch_drone）无 token 调用返回
 * {status:"requires_confirmation", action_id, action, summary:{title, rows:[{label,value}]}}
 * 时由 message-group.tsx 的 ToolCall 分支渲染本卡片。
 *
 * 批准/取消走同源 /api/uav/approval/{id}/{verb}（Next.js 服务端代理，
 * X-Admin-Key 只存在于服务端 env，不进浏览器）。批准成功拿到一次性
 * confirm_token 后派发 window 事件 "uav:system-confirmation"，由聊天页
 * 的监听器以隐藏 human 消息回发 [SYSTEM_CONFIRMATION] 指令给模型。
 *
 * 本文件真身在 正式开发/webui/，软链进 deerflow/frontend（gitignore 克隆）。
 */

import { useState } from "react";

export interface UavConfirmSummary {
  title?: string;
  rows?: { label: string; value: string }[];
}

export interface UavConfirmResult {
  status: string;
  action_id: string;
  action: string;
  summary?: UavConfirmSummary;
}

export function isUavConfirmation(v: unknown): v is UavConfirmResult {
  return (
    typeof v === "object" &&
    v !== null &&
    (v as { status?: unknown }).status === "requires_confirmation" &&
    typeof (v as { action_id?: unknown }).action_id === "string" &&
    typeof (v as { action?: unknown }).action === "string"
  );
}

type Phase = "pending" | "busy" | "approved" | "cancelled" | "error";

export function UavConfirmCard({ result }: { result: UavConfirmResult }) {
  const [phase, setPhase] = useState<Phase>("pending");
  const [error, setError] = useState<string>("");

  const call = async (verb: "approve" | "cancel") => {
    setPhase("busy");
    try {
      const r = await fetch("/api/uav/approval", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action_id: result.action_id, verb }),
      });
      const body = (await r.json()) as {
        confirm_token?: string;
        detail?: string;
      };
      if (!r.ok) {
        throw new Error(body.detail ?? `HTTP ${r.status}`);
      }
      if (verb === "approve") {
        window.dispatchEvent(
          new CustomEvent("uav:system-confirmation", {
            detail: {
              text: `[SYSTEM_CONFIRMATION] action=${result.action} action_id=${result.action_id} confirm_token=${body.confirm_token}`,
            },
          }),
        );
        setPhase("approved");
      } else {
        setPhase("cancelled");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPhase("error");
    }
  };

  const rows = result.summary?.rows ?? [];

  return (
    <div className="my-2 max-w-md rounded-lg border border-amber-400/60 bg-amber-50/50 p-3 text-sm dark:bg-amber-950/20">
      <div className="mb-2 font-medium text-amber-700 dark:text-amber-400">
        ⚠ 高危操作待人工确认{result.summary?.title ? ` · ${result.summary.title}` : ""}
      </div>
      {rows.length > 0 && (
        <table className="mb-2 w-full">
          <tbody>
            {rows.map((row) => (
              <tr key={row.label}>
                <td className="pr-3 align-top whitespace-nowrap opacity-60">
                  {row.label}
                </td>
                <td className="break-all">{row.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {phase === "pending" || phase === "busy" ? (
        <div className="flex gap-2">
          <button
            className="rounded-md bg-amber-600 px-3 py-1 text-white hover:bg-amber-700 disabled:opacity-50"
            disabled={phase === "busy"}
            onClick={() => void call("approve")}
          >
            确认执行
          </button>
          <button
            className="rounded-md border px-3 py-1 hover:bg-black/5 disabled:opacity-50 dark:hover:bg-white/10"
            disabled={phase === "busy"}
            onClick={() => void call("cancel")}
          >
            取消
          </button>
        </div>
      ) : phase === "approved" ? (
        <div className="text-green-700 dark:text-green-400">
          ✓ 已确认，指令已回传智能体执行
        </div>
      ) : phase === "cancelled" ? (
        <div className="opacity-60">已取消</div>
      ) : (
        <div className="text-red-600">
          确认失败：{error}（确认单可能已过期，请让智能体重新发起）
        </div>
      )}
      <div className="mt-2 text-xs opacity-50">
        高危操作需人工确认 · Agent 权限 ≤ 当前用户权限
      </div>
    </div>
  );
}
