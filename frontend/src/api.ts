import { fetchEventSource } from '@microsoft/fetch-event-source'
import type { AGUIEvent } from './types'

export async function runAgent(
  threadId: string,
  message: string,
  onEvent: (e: AGUIEvent) => void,
): Promise<void> {
  await fetchEventSource('/api/agent/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ thread_id: threadId, message }),
    openWhenHidden: true,
    onmessage(ev) {
      if (ev.data) onEvent(JSON.parse(ev.data) as AGUIEvent)
    },
  })
}

export async function approveAction(actionId: string): Promise<{ action: string; confirm_token: string }> {
  const r = await fetch(`/api/confirmations/${actionId}/approve`, { method: 'POST' })
  if (!r.ok) throw new Error((await r.json()).detail ?? '确认失败')
  return r.json()
}

export async function cancelAction(actionId: string): Promise<void> {
  await fetch(`/api/confirmations/${actionId}/cancel`, { method: 'POST' })
}

export async function fetchConfig(): Promise<{ agent_mode: string; model: string; mcp_servers: string[] }> {
  const r = await fetch('/api/config')
  return r.json()
}

export async function fetchTaskStatus(taskId: string): Promise<{ status: string; progress_pct: number }> {
  const r = await fetch(`/api/tasks/${taskId}`)
  return r.json()
}

export async function resetDemo(): Promise<void> {
  await fetch('/api/reset', { method: 'POST' })
}
