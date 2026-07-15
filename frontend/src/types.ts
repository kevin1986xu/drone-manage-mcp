// AG-UI 事件与业务数据类型（与后端 app/agui/events.py 对齐）

export type AGUIEvent =
  | { type: 'RUN_STARTED'; run_id: string }
  | { type: 'RUN_FINISHED'; run_id: string }
  | { type: 'RUN_ERROR'; message: string }
  | { type: 'TEXT_MESSAGE_START'; message_id: string }
  | { type: 'TEXT_MESSAGE_CONTENT'; message_id: string; delta: string }
  | { type: 'TEXT_MESSAGE_END'; message_id: string }
  | { type: 'TOOL_CALL_START'; tool_call_id: string; tool_name: string; args: Record<string, unknown> }
  | { type: 'TOOL_CALL_END'; tool_call_id: string; tool_name: string; result: unknown }
  | { type: 'VIEW_DIRECTIVE'; directive: ViewDirective; payload: Record<string, unknown> }

export type ViewDirective = 'show_map' | 'show_iframe' | 'show_report' | 'show_confirm'

export interface Plot {
  plot_id: string
  plot_type: string
  priority: string
  area_mu: number
  centroid: [number, number]
  geometry: GeoJSON.Polygon
}

export interface Drone {
  drone_id: string
  model: string
  battery_pct: number
  payload: string
  status_cn: string
  distance_km?: number
  location: GeoJSON.Point
}

export interface RouteInfo {
  route_id: string
  version: number
  length_km: number
  duration_min: number
  geometry: GeoJSON.LineString
  covered_plots: { plot_id: string; requested: boolean; coverage_rate: number }[]
}

export interface Check {
  item: string
  status: 'pass' | 'warn' | 'fail'
  detail: string
}

export interface ConfirmPayload {
  action_id: string
  action: string
  summary: { title: string; rows: { label: string; value: string }[] }
}

export interface FlightTask {
  flight_task_id: string
  drone_id: string
  route_id: string
  duration_min: number
}

export interface PlanSortie {
  plot_ids: string[]
  status: string
  route_id: string | null
  drone_id: string | null
}

export interface PlanDay {
  day: number
  sorties: PlanSortie[]
}

export interface TaskPlan {
  schedule: PlanDay[]
  plan_id?: string
  active?: boolean
  feasible?: boolean
}

// 左栏消息流条目
export type ChatItem =
  | { kind: 'user'; id: string; text: string }
  | { kind: 'agent'; id: string; text: string; streaming: boolean }
  | { kind: 'cot'; id: string; calls: CotCall[] }
  | { kind: 'event'; id: string; text: string }
  | { kind: 'confirm'; id: string; payload: ConfirmPayload; state: 'pending' | 'approved' | 'cancelled' }

export interface CotCall {
  tool_call_id: string
  tool_name: string
  label: string
  done: boolean
  ret?: string
}
