import { useEffect, useRef, useState } from 'react'
import { pushEvent, sendMessage } from '../agent'
import { approveAction, cancelAction } from '../api'
import { useStore } from '../store'
import type { ChatItem } from '../types'

// 演示动线提示话术（可自由输入，不限于这些）
const HINTS = [
  '帮我查一下光明区最新的下发图斑',
  '调度一下这些图斑周边有哪些无人机',
  '好，用 D-12 规划航线',
  '我手动调整一下航线',
  '我要起飞',
]

function ConfirmCard({ item }: { item: Extract<ChatItem, { kind: 'confirm' }> }) {
  const setConfirmState = useStore((s) => s.setConfirmState)
  const { payload, state } = item
  const act = async (ok: boolean) => {
    if (state !== 'pending') return
    if (ok) {
      try {
        const r = await approveAction(payload.action_id)
        setConfirmState(payload.action_id, 'approved')
        pushEvent(`human-in-the-loop ✓ · ${payload.action} 已人工确认`)
        await sendMessage(
          `[SYSTEM_CONFIRMATION] action=${r.action} action_id=${payload.action_id} confirm_token=${r.confirm_token}`,
          { hidden: true },
        )
      } catch (e) {
        pushEvent(`确认失败：${String(e)}`)
      }
    } else {
      await cancelAction(payload.action_id)
      setConfirmState(payload.action_id, 'cancelled')
      await sendMessage(`[SYSTEM_CANCELLED] action=${payload.action} action_id=${payload.action_id}`, {
        hidden: true,
      })
    }
  }
  return (
    <div className="card">
      <div className="card-head">
        <span className="warn-bar" />
        待确认 · {payload.summary.title}
      </div>
      <div className="card-body">
        {payload.summary.rows.map((r) => (
          <div className="card-row" key={r.label}>
            <span>{r.label}</span>
            <span>{r.value}</span>
          </div>
        ))}
      </div>
      <div className="card-acts">
        <button className="btn" disabled={state !== 'pending'} onClick={() => act(false)}>
          {state === 'cancelled' ? '已取消' : '取消'}
        </button>
        <button className="btn btn-go" disabled={state !== 'pending'} onClick={() => act(true)}>
          {state === 'approved' ? '已确认' : '确认执行'}
        </button>
      </div>
      <div className="card-note">高危操作需人工确认 · Agent 权限 ≤ 当前用户权限</div>
    </div>
  )
}

function Item({ item }: { item: ChatItem }) {
  switch (item.kind) {
    case 'user':
      return <div className="msg-u">{item.text}</div>
    case 'agent':
      return (
        <div className="msg-a">
          {item.text}
          {item.streaming && <span className="caret">▍</span>}
        </div>
      )
    case 'cot':
      return (
        <div className="cot">
          {item.calls.map((c) => (
            <div key={c.tool_call_id}>
              <div className="cot-row">
                <span className="tool">{c.tool_name}</span>
                <span className="lbl">{c.label}</span>
                <span className="st">{c.done ? <span className="ok-mark">✓</span> : <span className="spin" />}</span>
              </div>
              {c.done && c.ret && <div className="cot-ret">↳ {c.ret}</div>}
            </div>
          ))}
        </div>
      )
    case 'event':
      return (
        <div className="evt">
          ── <b>AG-UI</b> ▸ {item.text} ──
        </div>
      )
    case 'confirm':
      return <ConfirmCard item={item} />
  }
}

export function Chat() {
  const items = useStore((s) => s.items)
  const busy = useStore((s) => s.agentBusy)
  const hintIndex = useStore((s) => s.hintIndex)
  const [input, setInput] = useState('')
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [items])

  const hint = hintIndex < HINTS.length ? HINTS[hintIndex] : null
  const submit = (text: string) => {
    if (!text.trim() || busy) return
    setInput('')
    void sendMessage(text.trim())
  }

  return (
    <div className="chat">
      <div className="chat-head">
        <b>作业助手</b>
        <span>· 飞行作业智能体</span>
        <span className={`agent-state${busy ? ' think' : ''}`}>{busy ? '思考中' : '待命'}</span>
      </div>
      <div className="msgs">
        {items.map((it) => (
          <Item key={it.id} item={it} />
        ))}
        <div ref={endRef} />
      </div>
      <div className="composer">
        {hint && !busy && (
          <button className="hint-chip" onClick={() => submit(hint)}>
            💬 {hint}
          </button>
        )}
        <div className="in-row">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submit(input)}
            placeholder="输入指令，例如：帮我查一下光明区的图斑"
          />
          <button className="send" disabled={busy || !input.trim()} onClick={() => submit(input)}>
            发送
          </button>
        </div>
      </div>
    </div>
  )
}
