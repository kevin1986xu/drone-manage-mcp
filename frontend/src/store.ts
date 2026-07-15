import { create } from 'zustand'
import type {
  AGUIEvent, ChatItem, Check, ConfirmPayload, Drone, FlightTask, Plot, RouteInfo, TaskPlan,
} from './types'

// 工具调用在 CoT 中的展示文案
const TOOL_LABELS: Record<string, string> = {
  query_plots: '图斑查询',
  find_nearby_drones: '周边无人机查询',
  get_drone_status: '无人机状态查询',
  dispatch_drone: '无人机调度（人在环）',
  generate_route: '航线生成（多图斑覆盖）',
  get_route_detail: '航线详情',
  explain_route: '航线决策依据',
  open_route_editor: '打开航线编辑',
  check_weather: '气象条件',
  check_battery: '电量续航',
  check_route_obstacle: '航线避障',
  check_drone_obstacle: '机载避障',
  check_airspace: '空域许可',
  preflight_check: '飞前检查（聚合）',
  take_off: '起飞（人在环）',
}

function retSummary(tool: string, result: unknown): string | undefined {
  const r = result as Record<string, any>
  if (!r || typeof r !== 'object') return undefined
  if (r.error) return `⚠ ${r.error}`
  if (r.status === 'requires_confirmation') return '已生成待确认单，等待人工确认'
  if (r.status === 'rejected') return `⚠ 已拒绝：${r.reason ?? ''}`
  switch (tool) {
    case 'query_plots':
      return `命中 ${r.count} 个图斑${r.batch_no ? `（批次 ${r.batch_no}）` : ''}`
    case 'find_nearby_drones':
      return `${r.count} 架可用：${(r.drones ?? []).map((d: Drone) => d.drone_id).join(' / ')}`
    case 'generate_route':
      return `航线 ${r.route_id} · ${r.length_km} km · 预计 ${r.duration_min} min`
    case 'explain_route':
      return `覆盖图斑 ${r.decision?.covered_plots?.length ?? 0} 个 · 同航向带合并`
    case 'get_route_detail':
      return `rev.${r.version} · ${r.length_km} km · ${r.duration_min} min`
    case 'open_route_editor':
      return `免登录链接已生成 · token 有效期 ${r.token_ttl_min} min`
    case 'take_off':
      return r.status === 'airborne' ? `任务 ${r.flight_task_id} 已创建 · MQTT 遥测订阅中` : undefined
    case 'dispatch_drone':
      return r.order_id ? `调度单 ${r.order_id} · ${r.drone_id} 已锁定` : undefined
    case 'create_task_plan':
      if (r.status === 'plan_activated') return `计划 ${r.plan_id} 已生效 · 第1天 ${r.day1_executed} 架次已派`
      return undefined
    case 'get_plan_progress':
      return `${r.executed_sorties}/${r.total_sorties} 架次已执行`
    default:
      if (r.item && r.status) return r.detail
      return undefined
  }
}

export type StageView = 'map' | 'iframe' | 'report' | 'plan'

interface AppState {
  threadId: string
  items: ChatItem[]
  agentBusy: boolean
  hintIndex: number
  // 右栏
  view: StageView
  aguiTag: string
  stageSub: string
  plots: Plot[]
  drones: Drone[]
  route: RouteInfo | null
  highlightIds: string[]
  flight: (FlightTask & { progress: number; done: boolean }) | null
  checks: Check[]
  iframeUrl: string | null
  mapEngine: string
  plan: TaskPlan | null
  // actions
  handleEvent: (e: AGUIEvent) => void
  addUser: (text: string) => void
  setBusy: (b: boolean) => void
  setConfirmState: (actionId: string, state: 'approved' | 'cancelled') => void
  setFlightProgress: (progress: number, done: boolean) => void
  advanceHint: () => void
}

let uid = 0
const nid = () => `i${++uid}`

export const useStore = create<AppState>((set, get) => ({
  threadId: `web-${Math.random().toString(36).slice(2, 10)}`,
  items: [],
  agentBusy: false,
  hintIndex: 0,
  view: 'map',
  aguiTag: 'idle',
  stageSub: '等待智能体指令',
  plots: [],
  drones: [],
  route: null,
  highlightIds: [],
  flight: null,
  checks: [],
  iframeUrl: null,
  mapEngine: 'MapLibre GL · 2D',
  plan: null,

  addUser: (text) => set((s) => ({ items: [...s.items, { kind: 'user', id: nid(), text }] })),
  setBusy: (agentBusy) => set({ agentBusy }),
  advanceHint: () => set((s) => ({ hintIndex: s.hintIndex + 1 })),

  setConfirmState: (actionId, state) =>
    set((s) => ({
      items: s.items.map((it) =>
        it.kind === 'confirm' && it.payload.action_id === actionId ? { ...it, state } : it,
      ),
    })),

  setFlightProgress: (progress, done) =>
    set((s) => (s.flight ? { flight: { ...s.flight, progress, done } } : {})),

  handleEvent: (e) => {
    const s = get()
    switch (e.type) {
      case 'TEXT_MESSAGE_START':
        set({ items: [...s.items, { kind: 'agent', id: e.message_id, text: '', streaming: true }] })
        break
      case 'TEXT_MESSAGE_CONTENT':
        set({
          items: s.items.map((it) =>
            it.kind === 'agent' && it.id === e.message_id ? { ...it, text: it.text + e.delta } : it,
          ),
        })
        break
      case 'TEXT_MESSAGE_END':
        set({
          items: s.items.map((it) =>
            it.kind === 'agent' && it.id === e.message_id ? { ...it, streaming: false } : it,
          ),
        })
        break
      case 'TOOL_CALL_START': {
        const call = {
          tool_call_id: e.tool_call_id,
          tool_name: e.tool_name,
          label: TOOL_LABELS[e.tool_name] ?? e.tool_name,
          done: false,
        }
        const last = s.items[s.items.length - 1]
        if (last?.kind === 'cot') {
          set({
            items: s.items.map((it) => (it === last ? { ...last, calls: [...last.calls, call] } : it)),
          })
        } else {
          set({ items: [...s.items, { kind: 'cot', id: nid(), calls: [call] }] })
        }
        break
      }
      case 'TOOL_CALL_END':
        set({
          items: s.items.map((it) =>
            it.kind === 'cot'
              ? {
                  ...it,
                  calls: it.calls.map((c) =>
                    c.tool_call_id === e.tool_call_id
                      ? { ...c, done: true, ret: retSummary(e.tool_name, e.result) }
                      : c,
                  ),
                }
              : it,
          ),
        })
        break
      case 'VIEW_DIRECTIVE':
        applyDirective(e.directive, e.payload, set as never, get)
        break
      case 'RUN_ERROR':
        set({
          items: [...s.items, { kind: 'agent', id: nid(), text: `⚠ ${e.message}`, streaming: false }],
        })
        break
      default:
        break
    }
  },
}))

function evt(items: ChatItem[], text: string): ChatItem[] {
  return [...items, { kind: 'event', id: nid(), text }]
}

function applyDirective(
  directive: string,
  payload: Record<string, any>,
  set: (p: Partial<AppState>) => void,
  get: () => AppState,
) {
  const s = get()
  switch (directive) {
    case 'show_map': {
      const patch: Partial<AppState> = { view: 'map', aguiTag: 'show_map' }
      if (payload.layer === 'plots') {
        patch.plots = payload.plots
        patch.stageSub = `plots: ${payload.plots.length} features · GeoJSON`
        patch.items = evt(s.items, 'show_map · 图斑落图')
      } else if (payload.layer === 'drones') {
        patch.drones = payload.drones
        patch.stageSub = `plots: ${s.plots.length} · drones: ${payload.drones.length}`
        patch.items = evt(s.items, 'show_map · 无人机位置更新')
      } else if (payload.layer === 'route') {
        patch.route = payload.route
        patch.iframeUrl = null
        patch.stageSub = `route: ${payload.route.route_id} rev.${payload.route.version} · ${payload.route.length_km} km`
        patch.items = evt(s.items, 'show_map · 航线渲染')
      } else if (payload.layer === 'highlight') {
        patch.highlightIds = payload.plot_ids
        patch.stageSub = `route covers ${payload.plot_ids.length} plots`
      } else if (payload.layer === 'flight') {
        patch.flight = { ...(payload.task as FlightTask), progress: 0, done: false }
        patch.stageSub = `task: ${payload.task.flight_task_id} · telemetry 1 Hz`
        patch.items = evt(s.items, 'human-in-the-loop ✓ · take_off 指令下发')
      }
      set(patch)
      break
    }
    case 'show_iframe':
      set({
        view: 'iframe',
        aguiTag: 'show_iframe',
        iframeUrl: payload.url,
        stageSub: 'iframe · postMessage 通道就绪',
        items: evt(s.items, 'show_iframe · 嵌入航线编辑界面'),
      })
      break
    case 'show_report': {
      const checks = payload.mode === 'append' ? [...s.checks, payload.check] : payload.checks
      const first = s.view !== 'report'
      set({
        view: 'report',
        aguiTag: 'show_report',
        checks: first && payload.mode === 'append' ? [payload.check] : checks,
        stageSub: `preflight_check · ${first && payload.mode === 'append' ? 1 : checks.length} 项`,
        items: first ? evt(s.items, 'show_report · 飞前检查') : s.items,
      })
      break
    }
    case 'show_confirm':
      set({
        aguiTag: 'show_confirm',
        items: [
          ...s.items,
          { kind: 'confirm', id: nid(), payload: payload as ConfirmPayload, state: 'pending' },
        ],
      })
      break
    case 'show_plan': {
      const totalSorties = (payload.schedule as { sorties: unknown[] }[]).reduce((n, d) => n + d.sorties.length, 0)
      set({
        view: 'plan',
        aguiTag: 'show_plan',
        plan: payload as unknown as TaskPlan,
        stageSub: `批量排期 · ${payload.schedule.length} 天 / ${totalSorties} 架次${payload.active ? ' · 执行中' : ' · 待确认'}`,
        items: evt(s.items, `show_plan · 逐日排期${payload.active ? '（已生效）' : '（待确认）'}`),
      })
      break
    }
  }
}
