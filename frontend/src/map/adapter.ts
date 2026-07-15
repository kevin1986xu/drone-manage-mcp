/**
 * 地图接口封装层（关键架构决策，D1 即定）：
 * 上层只依赖 addPlots / drawDrones / drawRoute / highlight / flyTo 等自有接口，
 * 底下 MapLibre（演示期 2D）/ Cesium（生产期 3D）/ SuperMap iClient3D 可替换。
 */
import maplibregl, { LngLatBounds, Map as MLMap, Marker } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { Drone, Plot, RouteInfo } from '../types'
import { Canvas2DAdapter, shortId } from './canvas2d'
import type { IMapAdapter } from './types'

/** 工厂：优先 MapLibre（WebGL）；不可用时降级 Canvas 2D，演示不中断 */
export function createMapAdapter(container: HTMLElement): IMapAdapter {
  try {
    const probe = document.createElement('canvas')
    const gl = probe.getContext('webgl2') ?? probe.getContext('webgl')
    if (!gl) throw new Error('WebGL unavailable')
    return new MapAdapter(container)
  } catch {
    return new Canvas2DAdapter(container)
  }
}

const COLORS = {
  plot: '#E0A63C',
  plotSoft: 'rgba(224,166,60,.14)',
  hot: '#3EC6B8',
  hotSoft: 'rgba(62,198,184,.13)',
  route: '#3EC6B8',
}

function baseStyle(): maplibregl.StyleSpecification {
  const style: maplibregl.StyleSpecification = {
    version: 8,
    sources: {},
    layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#0F161E' } }],
  }
  const tdtKey = import.meta.env.VITE_TIANDITU_KEY as string | undefined
  if (tdtKey) {
    // 天地图影像 WMTS（演示现场建议瓦片本地缓存防断网）
    style.sources.basemap = {
      type: 'raster',
      tiles: [
        `https://t0.tianditu.gov.cn/img_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=img&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk=${tdtKey}`,
      ],
      tileSize: 256,
    }
    style.layers.push({ id: 'basemap', type: 'raster', source: 'basemap', paint: { 'raster-opacity': 0.55 } })
  } else if (import.meta.env.VITE_BASEMAP !== 'none') {
    style.sources.basemap = {
      type: 'raster',
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: '© OpenStreetMap',
    }
    // 压暗底图贴合控制台暗色主题；离线时瓦片加载失败自动回落为深色背景
    style.layers.push({
      id: 'basemap',
      type: 'raster',
      source: 'basemap',
      paint: { 'raster-opacity': 0.35, 'raster-saturation': -0.7 },
    })
  }
  return style
}

const EMPTY_FC = { type: 'FeatureCollection', features: [] } as GeoJSON.FeatureCollection

export class MapAdapter implements IMapAdapter {
  readonly engine = 'MapLibre GL · 2D'
  private map: MLMap
  private ready = false
  private queued: (() => void)[] = []
  private plotLabels: Marker[] = []
  private droneMarkers = new Map<string, Marker>()
  private flightMarker: Marker | null = null
  private routeCoords: [number, number][] = []
  private hiddenDrone: string | null = null

  constructor(container: HTMLElement) {
    this.map = new maplibregl.Map({
      container,
      style: baseStyle(),
      center: [113.94, 22.7425],
      zoom: 12.5,
      attributionControl: false,
    })
    this.map.on('load', () => {
      this.initLayers()
      this.ready = true
      this.queued.forEach((f) => f())
      this.queued = []
    })
  }

  private when(f: () => void) {
    this.ready ? f() : this.queued.push(f)
  }

  private initLayers() {
    this.map.addSource('plots', { type: 'geojson', data: EMPTY_FC, promoteId: 'plot_id' })
    this.map.addSource('route', { type: 'geojson', data: EMPTY_FC })
    this.map.addLayer({
      id: 'plots-fill', type: 'fill', source: 'plots',
      paint: {
        'fill-color': ['case', ['boolean', ['feature-state', 'hot'], false], COLORS.hotSoft, COLORS.plotSoft],
      },
    })
    this.map.addLayer({
      id: 'plots-line', type: 'line', source: 'plots',
      paint: {
        'line-color': ['case', ['boolean', ['feature-state', 'hot'], false], COLORS.hot, COLORS.plot],
        'line-width': ['case', ['boolean', ['feature-state', 'hot'], false], 2, 1.4],
      },
    })
    this.map.addLayer({
      id: 'route-line', type: 'line', source: 'route',
      paint: { 'line-color': COLORS.route, 'line-width': 2, 'line-dasharray': [2.2, 1.6] },
    })
    this.map.addLayer({
      id: 'route-points', type: 'circle', source: 'route',
      filter: ['==', ['geometry-type'], 'Point'],
      paint: {
        'circle-radius': 3.2,
        'circle-color': ['case', ['get', 'terminal'], '#ffffff', COLORS.route],
      },
    })
  }

  addPlots(plots: Plot[]) {
    this.when(() => {
      const fc: GeoJSON.FeatureCollection = {
        type: 'FeatureCollection',
        features: plots.map((p) => ({
          type: 'Feature',
          properties: { plot_id: p.plot_id },
          geometry: p.geometry,
        })),
      }
      ;(this.map.getSource('plots') as maplibregl.GeoJSONSource).setData(fc)
      this.plotLabels.forEach((m) => m.remove())
      this.plotLabels = plots.map((p) => {
        const el = document.createElement('div')
        el.className = 'plot-label'
        el.dataset.plotId = p.plot_id
        el.textContent = `图斑 ${shortId(p.plot_id)}`
        const ring = p.geometry.coordinates[0]
        return new Marker({ element: el, anchor: 'bottom' })
          .setLngLat([ring[0][0], ring[0][1]])
          .addTo(this.map)
      })
      if (plots.length) this.fit(plots.flatMap((p) => p.geometry.coordinates[0]) as [number, number][])
    })
  }

  drawDrones(drones: Drone[]) {
    this.when(() => {
      for (const [id, m] of this.droneMarkers) {
        if (!drones.some((d) => d.drone_id === id)) {
          m.remove()
          this.droneMarkers.delete(id)
        }
      }
      drones.forEach((d) => {
        const coords = d.location?.coordinates
        if (!coords || coords.length < 2) return // 无位置的设备跳过，不落图
        let m = this.droneMarkers.get(d.drone_id)
        if (!m) {
          const el = document.createElement('div')
          el.className = 'drone-marker'
          const bat = d.battery_pct != null ? `${d.battery_pct}%` : d.status_cn
          el.innerHTML = `<span class="drone-dot"></span><span class="drone-tag">${d.drone_id} · ${bat}</span>`
          // MapLibre 要求先 setLngLat 再 addTo，否则 addTo 读取未定义坐标报错
          m = new Marker({ element: el }).setLngLat(coords as [number, number]).addTo(this.map)
          this.droneMarkers.set(d.drone_id, m)
        } else {
          m.setLngLat(coords as [number, number])
        }
        m.getElement().style.display = this.hiddenDrone === d.drone_id ? 'none' : ''
      })
    })
  }

  drawRoute(route: RouteInfo) {
    this.when(() => {
      this.routeCoords = route.geometry.coordinates as [number, number][]
      const fc: GeoJSON.FeatureCollection = {
        type: 'FeatureCollection',
        features: [
          { type: 'Feature', properties: {}, geometry: route.geometry },
          ...this.routeCoords.map((c, i) => ({
            type: 'Feature' as const,
            properties: { terminal: i === 0 || i === this.routeCoords.length - 1 },
            geometry: { type: 'Point' as const, coordinates: c },
          })),
        ],
      }
      ;(this.map.getSource('route') as maplibregl.GeoJSONSource).setData(fc)
      this.fit(this.routeCoords)
    })
  }

  highlight(plotIds: string[]) {
    this.when(() => {
      this.plotLabels.forEach((m) => {
        const id = m.getElement().dataset.plotId ?? ''
        m.getElement().classList.toggle('hot', plotIds.includes(id))
      })
      const src = this.map.getSource('plots') as maplibregl.GeoJSONSource | undefined
      if (!src) return
      // feature-state 高亮
      const data = (src as unknown as { _data: GeoJSON.FeatureCollection })._data
      data.features?.forEach((f) => {
        const id = f.properties?.plot_id as string
        this.map.setFeatureState({ source: 'plots', id }, { hot: plotIds.includes(id) })
      })
    })
  }

  /** 飞行动画：按任务进度把执行中的无人机放到航线对应位置 */
  setFlightProgress(droneId: string, progressPct: number, done: boolean) {
    this.when(() => {
      if (!this.routeCoords.length) return
      if (done) {
        this.flightMarker?.remove()
        this.flightMarker = null
        this.hiddenDrone = null
        const m = this.droneMarkers.get(droneId)
        if (m) m.getElement().style.display = ''
        return
      }
      this.hiddenDrone = droneId
      this.droneMarkers.get(droneId)?.getElement().style.setProperty('display', 'none')
      if (!this.flightMarker) {
        const el = document.createElement('div')
        el.className = 'drone-marker flying'
        el.innerHTML = `<span class="drone-dot hot"></span><span class="drone-tag hot" id="flight-tag"></span>`
        this.flightMarker = new Marker({ element: el }).setLngLat(this.routeCoords[0]).addTo(this.map)
      }
      const pos = pointAt(this.routeCoords, progressPct / 100)
      this.flightMarker.setLngLat(pos)
      const tag = this.flightMarker.getElement().querySelector('#flight-tag')
      if (tag) tag.textContent = `${droneId} 执行中 ${Math.round(progressPct)}%`
    })
  }

  private fit(coords: [number, number][]) {
    const b = coords.reduce((acc, c) => acc.extend(c), new LngLatBounds(coords[0], coords[0]))
    this.map.fitBounds(b, { padding: 70, duration: 800, maxZoom: 14.5 })
  }

  destroy() {
    this.map.remove()
  }
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
