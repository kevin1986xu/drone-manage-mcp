import { useEffect, useRef, useState } from 'react'
import { createMapAdapter } from '../map/adapter'
import type { IMapAdapter } from '../map/types'
import { useStore } from '../store'

export function MapView() {
  const ref = useRef<HTMLDivElement>(null)
  const adapterRef = useRef<IMapAdapter | null>(null)
  const [engine, setEngine] = useState('')

  const plots = useStore((s) => s.plots)
  const drones = useStore((s) => s.drones)
  const route = useStore((s) => s.route)
  const highlightIds = useStore((s) => s.highlightIds)
  const flight = useStore((s) => s.flight)

  useEffect(() => {
    if (!ref.current) return
    try {
      adapterRef.current = createMapAdapter(ref.current)
      setEngine(adapterRef.current.engine)
      useStore.setState({ mapEngine: adapterRef.current.engine })
    } catch (e) {
      console.error('地图初始化失败', e)
    }
    return () => {
      adapterRef.current?.destroy()
      adapterRef.current = null
    }
  }, [])

  useEffect(() => {
    if (plots.length) adapterRef.current?.addPlots(plots)
  }, [plots])
  useEffect(() => {
    if (drones.length) adapterRef.current?.drawDrones(drones)
  }, [drones])
  useEffect(() => {
    if (route) adapterRef.current?.drawRoute(route)
  }, [route])
  useEffect(() => {
    adapterRef.current?.highlight(highlightIds)
  }, [highlightIds])
  useEffect(() => {
    if (flight) adapterRef.current?.setFlightProgress(flight.drone_id, flight.progress, flight.done)
  }, [flight])

  return (
    <div className="map-wrap">
      <div ref={ref} className="map-canvas" style={{ position: 'relative' }} />
      {engine.includes('降级') && <div className="engine-badge">{engine} · WebGL 不可用</div>}
      {(plots.length > 0 || route) && (
        <div className="legend">
          <div>
            <span className="lg-plot" />
            下发图斑
          </div>
          {drones.length > 0 && (
            <div>
              <span className="lg-drone" />
              可用无人机
            </div>
          )}
          {route && (
            <div>
              <span className="lg-route" />
              规划航线 {route.route_id}
              {route.version > 1 ? ` rev.${route.version}` : ''}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
