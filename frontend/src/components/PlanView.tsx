import { useStore } from '../store'
import { shortId } from '../map/canvas2d'

const STATUS: Record<string, { txt: string; cls: string }> = {
  scheduled: { txt: '待执行', cls: 'sc' },
  dispatched: { txt: '已派飞', cls: 'ok' },
  route_ready: { txt: '航线就绪', cls: 'ok' },
  completed: { txt: '已完成', cls: 'ok' },
  queued: { txt: '排队待机', cls: 'warn' },
  route_failed: { txt: '规划失败', cls: 'bad' },
}

export function PlanView() {
  const plan = useStore((s) => s.plan)
  if (!plan) return null
  const totalSorties = plan.schedule.reduce((n, d) => n + d.sorties.length, 0)
  const done = plan.schedule.flatMap((d) => d.sorties).filter((x) => ['dispatched', 'route_ready', 'completed'].includes(x.status)).length

  return (
    <div className="plan-wrap">
      <div className="plan">
        <h3>批量核查排期{plan.plan_id ? ` · ${plan.plan_id}` : ''}</h3>
        <div className="p-sub">
          create_task_plan · {plan.schedule.length} 天 / {totalSorties} 架次
          {plan.active ? ` · 已执行 ${done}/${totalSorties}` : plan.feasible === false ? ' · ⚠ 超出截止约束' : ' · 待人工确认'}
        </div>
        {plan.schedule.map((d) => (
          <div className="p-day" key={d.day}>
            <div className="p-day-head">第 {d.day} 天 · {d.sorties.length} 架次</div>
            {d.sorties.map((s, i) => (
              <div className="p-sortie" key={i}>
                <span className="p-idx">架次{i + 1}</span>
                <span className="p-plots">{s.plot_ids.map(shortId).join('、')}</span>
                {s.drone_id && <span className="p-drone">{s.drone_id}</span>}
                <span className={`p-pill ${STATUS[s.status]?.cls ?? 'sc'}`}>{STATUS[s.status]?.txt ?? s.status}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}
