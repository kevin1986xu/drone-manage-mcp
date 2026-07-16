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

// 串行队列：Agent 忙时新消息排队、当前轮结束后依次发送，绝不静默丢弃。
// 关键场景：用户在 Agent 还在流式输出时点击确认卡片——[SYSTEM_CONFIRMATION]
// 若被丢弃，confirm_token 永远到不了 Agent，起飞会卡死在"已确认但未执行"。
let chain: Promise<void> = Promise.resolve()

export function sendMessage(text: string, opts: { hidden?: boolean } = {}): Promise<void> {
  if (!text.trim()) return Promise.resolve()
  chain = chain.then(() => deliver(text, opts)).catch(() => undefined)
  return chain
}

async function deliver(text: string, opts: { hidden?: boolean }): Promise<void> {
  const { threadId, addUser, setBusy, handleEvent, advanceHint } = useStore.getState()
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
