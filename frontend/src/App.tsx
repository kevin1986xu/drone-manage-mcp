import { useEffect, useRef, useState } from 'react'
import { sendMessage } from './agent'
import { fetchConfig, fetchTaskStatus, resetDemo } from './api'
import { Chat } from './components/Chat'
import { Stage } from './components/Stage'
import { useStore } from './store'

export function App() {
  const flight = useStore((s) => s.flight)
  const setFlightProgress = useStore((s) => s.setFlightProgress)
  const [cfg, setCfg] = useState<{ agent_mode: string; model: string; mcp_servers: string[] } | null>(null)
  const completedNotified = useRef(false)

  useEffect(() => {
    fetchConfig().then(setCfg).catch(() => setCfg(null))
  }, [])

  // 起飞后轮询任务进度（后端 1min 任务 = 1s 演示加速），驱动地图飞行动画
  useEffect(() => {
    if (!flight || flight.done) return
    const timer = setInterval(async () => {
      try {
        const st = await fetchTaskStatus(flight.flight_task_id)
        const done = st.status === 'completed'
        setFlightProgress(st.progress_pct, done)
        if (done && !completedNotified.current) {
          completedNotified.current = true
          clearInterval(timer)
          void sendMessage(
            `[TASK_COMPLETED] flight_task_id=${flight.flight_task_id} 任务已完成，影像已入库`,
            { hidden: true },
          )
        }
      } catch {
        /* 轮询失败忽略，下一轮重试 */
      }
    }, 1000)
    return () => clearInterval(timer)
  }, [flight?.flight_task_id, flight?.done])

  const reset = async () => {
    await resetDemo().catch(() => undefined)
    window.location.reload()
  }

  return (
    <div id="app">
      <div className="topbar">
        <div className="brand">
          <span className="dot" />
          低空智察<small>· 智能体控制台</small>
        </div>
        <div className="chips">
          <span className="chip">
            <i />
            MCP&nbsp;{cfg?.mcp_servers.length ?? 3}&nbsp;servers
          </span>
          <span className="chip">
            <i />
            {cfg ? cfg.model : '…'}
          </span>
          <span className="chip">
            <i />
            AG-UI&nbsp;SSE
          </span>
        </div>
        <button className="btn-reset" onClick={reset}>
          重新演示
        </button>
      </div>
      <div className="main">
        <Chat />
        <Stage />
      </div>
    </div>
  )
}
