import { useStore } from '../store'

const ICONS: Record<string, string> = {
  气象条件: '☀',
  电量续航: '▮',
  航线避障: '⛰',
  机载避障: '◎',
  空域许可: '▲',
}
const PILL: Record<string, { cls: string; txt: string }> = {
  pass: { cls: 'ok', txt: '通过' },
  warn: { cls: 'warn', txt: '注意' },
  fail: { cls: 'bad', txt: '未通过' },
}

export function ReportView() {
  const checks = useStore((s) => s.checks)
  const route = useStore((s) => s.route)
  const flightDrone = useStore((s) => s.drones)

  const droneId = flightDrone.find((d) => d.payload.includes('激光雷达'))?.drone_id ?? ''
  return (
    <div className="report-wrap">
      <div className="report">
        <h3>飞前安全检查{droneId ? ` · ${droneId}` : ''}</h3>
        <div className="r-sub">
          preflight_check{route ? ` · 航线 ${route.route_id} rev.${route.version}` : ''} ·{' '}
          {new Date().toLocaleString('zh-CN', { hour12: false })}
        </div>
        <div>
          {checks.map((c, i) => (
            <div className="r-item show" key={`${c.item}-${i}`}>
              <div className="r-ico">{ICONS[c.item] ?? '·'}</div>
              <div className="r-main">
                <b>{c.item}</b>
                <span>{c.detail}</span>
              </div>
              <span className={`pill ${PILL[c.status]?.cls ?? 'ok'}`}>{PILL[c.status]?.txt ?? c.status}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
