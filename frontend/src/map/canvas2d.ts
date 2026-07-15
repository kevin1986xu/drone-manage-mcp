/**
 * Canvas 2D 降级渲染器：WebGL 不可用（虚拟机/远程桌面/驱动受限的政企环境）
 * 时自动启用，保证演示不中断。实现与 MapLibre 版相同的 IMapAdapter 接口。
 */
import type { Drone, Plot, RouteInfo } from '../types'
import type { IMapAdapter } from './types'

const MONO = 'ui-monospace, "SF Mono", Menlo, Consolas, monospace'

interface State {
  plots: Plot[]
  drones: Drone[]
  route: RouteInfo | null
  highlight: Set<string>
  flight: { droneId: string; pct: number } | null
}

export class Canvas2DAdapter implements IMapAdapter {
  readonly engine = 'Canvas 2D（降级）'
  private cv: HTMLCanvasElement
  private raf = 0
  private s: State = { plots: [], drones: [], route: null, highlight: new Set(), flight: null }

  constructor(private container: HTMLElement) {
    this.cv = document.createElement('canvas')
    this.cv.style.cssText = 'position:absolute;inset:0;width:100%;height:100%'
    container.appendChild(this.cv)
    const loop = (t: number) => {
      this.render(t)
      this.raf = requestAnimationFrame(loop)
    }
    this.raf = requestAnimationFrame(loop)
  }

  addPlots(plots: Plot[]) {
    this.s.plots = plots
  }
  drawDrones(drones: Drone[]) {
    this.s.drones = drones
  }
  drawRoute(route: RouteInfo) {
    this.s.route = route
  }
  highlight(plotIds: string[]) {
    this.s.highlight = new Set(plotIds)
  }
  setFlightProgress(droneId: string, progressPct: number, done: boolean) {
    this.s.flight = done ? null : { droneId, pct: progressPct }
  }
  destroy() {
    cancelAnimationFrame(this.raf)
    this.cv.remove()
  }

  // ── 投影：fit 全部要素 ──
  private proj(w: number, h: number) {
    const pts: [number, number][] = [
      ...this.s.plots.flatMap((p) => p.geometry.coordinates[0] as [number, number][]),
      ...this.s.drones.map((d) => d.location.coordinates as [number, number]),
      ...((this.s.route?.geometry.coordinates as [number, number][]) ?? []),
    ]
    if (!pts.length) pts.push([113.92, 22.725], [113.96, 22.76])
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity
    pts.forEach(([x, y]) => {
      x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y)
    })
    const kx = Math.cos((((y0 + y1) / 2) * Math.PI) / 180)
    const pad = 60
    const sc = Math.min((w - 2 * pad) / ((x1 - x0) * kx || 1), (h - 2 * pad) / (y1 - y0 || 1))
    const ox = (w - (x1 - x0) * kx * sc) / 2
    const oy = (h - (y1 - y0) * sc) / 2
    return ([lon, lat]: [number, number] | number[]): [number, number] => [
      ox + (lon - x0) * kx * sc,
      h - oy - (lat - y0) * sc,
    ]
  }

  private render(t: number) {
    const dpr = devicePixelRatio || 1
    const r = this.container.getBoundingClientRect()
    if (r.width === 0) return
    if (this.cv.width !== r.width * dpr || this.cv.height !== r.height * dpr) {
      this.cv.width = r.width * dpr
      this.cv.height = r.height * dpr
    }
    const ctx = this.cv.getContext('2d')!
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    const w = r.width, h = r.height
    const P = this.proj(w, h)

    ctx.fillStyle = '#0F161E'
    ctx.fillRect(0, 0, w, h)
    ctx.strokeStyle = '#151F29'
    ctx.lineWidth = 1
    for (let x = 0; x < w; x += 44) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke() }
    for (let y = 0; y < h; y += 44) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke() }

    // 图斑
    this.s.plots.forEach((p) => {
      const hot = this.s.highlight.has(p.plot_id)
      const ring = p.geometry.coordinates[0]
      ctx.beginPath()
      ring.forEach((c, i) => {
        const q = P(c)
        i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1])
      })
      ctx.closePath()
      ctx.fillStyle = hot ? 'rgba(62,198,184,.13)' : 'rgba(224,166,60,.14)'
      ctx.strokeStyle = hot ? '#3EC6B8' : '#E0A63C'
      ctx.lineWidth = hot ? 2 : 1.4
      ctx.fill()
      ctx.stroke()
      const q = P(ring[0])
      ctx.fillStyle = hot ? '#7FDFD5' : '#C9A050'
      ctx.font = `11px ${MONO}`
      ctx.fillText(`图斑 ${shortId(p.plot_id)}`, q[0], q[1] - 7)
    })

    // 航线
    const coords = (this.s.route?.geometry.coordinates as [number, number][]) ?? []
    if (coords.length) {
      ctx.strokeStyle = '#3EC6B8'
      ctx.lineWidth = 2
      ctx.setLineDash([9, 6])
      ctx.lineDashOffset = -t / 50
      ctx.beginPath()
      coords.forEach((c, i) => {
        const q = P(c)
        i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1])
      })
      ctx.stroke()
      ctx.setLineDash([])
      coords.forEach((c, i) => {
        const q = P(c)
        ctx.beginPath()
        ctx.arc(q[0], q[1], 3, 0, 7)
        ctx.fillStyle = i === 0 || i === coords.length - 1 ? '#fff' : '#3EC6B8'
        ctx.fill()
      })
      const st = P(coords[0])
      ctx.fillStyle = '#7FDFD5'
      ctx.font = `11px ${MONO}`
      ctx.fillText('起降点', st[0] + 9, st[1] + 13)
    }

    // 无人机
    const pulse = (Math.sin(t / 500) + 1) / 2
    this.s.drones.forEach((d) => {
      if (this.s.flight && this.s.flight.droneId === d.drone_id) return
      const q = P(d.location.coordinates)
      ctx.beginPath()
      ctx.arc(q[0], q[1], 13 + pulse * 5, 0, 7)
      ctx.strokeStyle = `rgba(232,180,76,${0.35 - pulse * 0.2})`
      ctx.lineWidth = 1.5
      ctx.stroke()
      drawDroneIcon(ctx, q[0], q[1], 7, false)
      ctx.fillStyle = '#C7B183'
      ctx.font = `11px ${MONO}`
      const bat = d.battery_pct != null ? `${d.battery_pct}%` : d.status_cn
      ctx.fillText(`${d.drone_id} · ${bat}`, q[0] + 16, q[1] + 4)
    })

    // 执行中的无人机（沿航线推进）
    if (this.s.flight && coords.length) {
      const pos = P(pointAt(coords, this.s.flight.pct / 100))
      ctx.beginPath()
      ctx.arc(pos[0], pos[1], 16, 0, 7)
      ctx.strokeStyle = 'rgba(62,198,184,.4)'
      ctx.stroke()
      drawDroneIcon(ctx, pos[0], pos[1], 8, true)
      ctx.fillStyle = '#7FDFD5'
      ctx.font = `11px ${MONO}`
      ctx.fillText(`${this.s.flight.droneId} 执行中 ${Math.round(this.s.flight.pct)}%`, pos[0] + 18, pos[1] - 8)
    }
  }
}

/** 长业务编号（如 汉川市-变更调查-20260525-00003）取尾部两段作地图短标签 */
export function shortId(id: string): string {
  const parts = id.split('-')
  return parts.length > 2 ? parts.slice(-2).join('-') : id
}

function drawDroneIcon(ctx: CanvasRenderingContext2D, x: number, y: number, r: number, hot: boolean) {
  ctx.save()
  ctx.translate(x, y)
  const c = hot ? '#3EC6B8' : '#E8B44C'
  ctx.strokeStyle = c
  ctx.fillStyle = c
  ctx.lineWidth = 1.5
  for (const [a, b] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
    ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(a * r, b * r); ctx.stroke()
    ctx.beginPath(); ctx.arc(a * r, b * r, r * 0.42, 0, 7); ctx.stroke()
  }
  ctx.beginPath(); ctx.arc(0, 0, r * 0.5, 0, 7); ctx.fill()
  ctx.restore()
}

function pointAt(coords: [number, number][], t: number): [number, number] {
  const seg = (a: [number, number], b: [number, number]) =>
    Math.hypot((b[0] - a[0]) * Math.cos((a[1] * Math.PI) / 180), b[1] - a[1])
  const total = coords.slice(1).reduce((s, c, i) => s + seg(coords[i], c), 0)
  let d = t * total
  for (let i = 1; i < coords.length; i++) {
    const L = seg(coords[i - 1], coords[i])
    if (d <= L) {
      const k = L ? d / L : 0
      return [
        coords[i - 1][0] + (coords[i][0] - coords[i - 1][0]) * k,
        coords[i - 1][1] + (coords[i][1] - coords[i - 1][1]) * k,
      ]
    }
    d -= L
  }
  return coords[coords.length - 1]
}
