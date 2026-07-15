import type { Drone, Plot, RouteInfo } from '../types'

/** 地图封装层接口：上层只依赖它，底下 MapLibre / Canvas2D / Cesium 可替换 */
export interface IMapAdapter {
  addPlots(plots: Plot[]): void
  drawDrones(drones: Drone[]): void
  drawRoute(route: RouteInfo): void
  highlight(plotIds: string[]): void
  setFlightProgress(droneId: string, progressPct: number, done: boolean): void
  destroy(): void
  readonly engine: string
}
