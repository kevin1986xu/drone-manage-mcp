// 发送用户/系统消息给 Agent 并把 AG-UI 事件灌入 store
import { runAgent } from './api'
import { useStore } from './store'
import type { ChatItem } from './types'

let seq = 0

export function pushEvent(text: string) {
  useStore.setState((s) => ({
    items: [...s.items, { kind: 'event', id: `evt-${++seq}`, text } as ChatItem],
  }))
}

export async function sendMessage(text: string, opts: { hidden?: boolean } = {}): Promise<void> {
  const { threadId, agentBusy, addUser, setBusy, handleEvent, advanceHint } = useStore.getState()
  if (agentBusy || !text.trim()) return
  if (!opts.hidden) {
    addUser(text)
    advanceHint()
  }
  setBusy(true)
  try {
    await runAgent(threadId, text, handleEvent)
  } catch (err) {
    handleEvent({ type: 'RUN_ERROR', message: `连接后端失败：${String(err)}` })
  } finally {
    setBusy(false)
  }
}
