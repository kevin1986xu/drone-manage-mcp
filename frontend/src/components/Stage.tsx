import { useStore } from '../store'
import { EditorView } from './EditorView'
import { MapView } from './MapView'
import { PlanView } from './PlanView'
import { ReportView } from './ReportView'

const TITLES = { map: '作业地图', iframe: '航线编辑（免登录嵌入）', report: '飞前安全检查', plan: '批量核查排期' }

export function Stage() {
  const view = useStore((s) => s.view)
  const aguiTag = useStore((s) => s.aguiTag)
  const stageSub = useStore((s) => s.stageSub)
  const route = useStore((s) => s.route)
  const flight = useStore((s) => s.flight)
  const mapEngine = useStore((s) => s.mapEngine)

  const title = view === 'iframe' && route ? `航线编辑 · ${route.route_id}（免登录嵌入）` : TITLES[view]
  return (
    <div className="stage">
      <div className="stage-head">
        <span className="stage-title">{title}</span>
        <span className="agui-tag">AG-UI ▸ {aguiTag}</span>
        <span className="stage-sub">{stageSub}</span>
      </div>
      <div className="stage-body">
        {/* 地图常驻（保持 WebGL 上下文），其余视图覆盖其上 */}
        <div className={`view${view === 'map' ? ' on' : ''}`}>
          <MapView />
        </div>
        <div className={`view overlay${view === 'iframe' ? ' on' : ''}`}>
          <EditorView />
        </div>
        <div className={`view overlay${view === 'report' ? ' on' : ''}`}>
          <ReportView />
        </div>
        <div className={`view overlay${view === 'plan' ? ' on' : ''}`}>
          <PlanView />
        </div>
      </div>
      <div className="statusbar">
        <span>CGCS2000 / EPSG:4490</span>
        <span>
          {flight && !flight.done
            ? `航速 8.0 m/s · 高度 120 m · 进度 ${Math.round(flight.progress)}%`
            : mapEngine}
        </span>
        <span className={`task${flight ? (flight.done ? ' done' : ' run') : ''}`}>
          <i />
          {flight
            ? flight.done
              ? `任务 ${flight.flight_task_id} 已完成`
              : `任务 ${flight.flight_task_id} 执行中`
            : '无任务'}
        </span>
      </div>
    </div>
  )
}
