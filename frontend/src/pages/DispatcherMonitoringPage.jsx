import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { apiFetch } from '../api/client'
import { WORKFLOW_UPDATED } from '../api/signals'
import { monitoringPopulation, recentAttentionSignals } from '../dashboard/dispatcherMonitoring'
import MoscowOkrugsMap from '../components/MoscowOkrugsMap'
import MapControlsMenu from '../components/MapControlsMenu'
import ReplayControls from '../components/ReplayControls'
import DispatcherDashboardWidgets from '../dashboard/DispatcherDashboardWidgets'
import DashboardKpiRow from '../dashboard/DashboardKpiRow'
import { useReplayRuntime } from '../hooks/useReplayRuntime'

export default function DispatcherMonitoringPage({ mapControlsTarget }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [features, setFeatures] = useState([])
  const [area, setArea] = useState(null)
  const [showAll, setShowAll] = useState(false)
  const [retry, setRetry] = useState(0)
  const [kpiData, setKpiData] = useState({ objectCount: null, activeRequestsCount: null, snapshotKey: null })
  const { status, loading: replayLoading, error: replayError } = useReplayRuntime()
  useEffect(() => {
    let active = true
    fetch('/geo/moscow-okrugs.geojson').then(r => { if (!r.ok) throw new Error(); return r.json() })
      .then(value => { if (active) setFeatures(value.features ?? []) }).catch(() => {})
    return () => { active = false }
  }, [retry])
  useEffect(() => {
    let active = true
    const refresh = () => apiFetch('/signals/monitoring').then(value => { if (active) { setData(value); setError(null) } })
      .catch(e => { if (active) setError(e.status === 403 ? 'Раздел доступен диспетчерам.' : 'Не удалось загрузить ситуации. Состояние объектов неизвестно.') })
    refresh()
    const timer = setInterval(refresh, status?.mode === 'replay' ? 1500 : 30000)
    window.addEventListener(WORKFLOW_UPDATED, refresh)
    return () => { active = false; clearInterval(timer); window.removeEventListener(WORKFLOW_UPDATED, refresh) }
  }, [retry, status?.mode])
  const population = useMemo(() => monitoringPopulation(data, features, area, showAll), [data, features, area, showAll])
  const latestAttentionSignals = useMemo(() => recentAttentionSignals(population.signals), [population.signals])
  return <div className="dashboard-page"><div className="dashboard-workspace">
    <section className="dashboard-map-region" aria-label="Ключевые показатели и схема объектов">
      <DashboardKpiRow
        objectCount={kpiData.objectCount}
        activeRequestsCount={kpiData.activeRequestsCount}
        snapshotKey={kpiData.snapshotKey}
      />
      <div className="dashboard-map-layer">
        <MoscowOkrugsMap
          objects={error ? [] : population.objects}
          selectedArea={area}
          onSelectionChange={setArea}
          replayMode={status?.mode === 'replay'}
          snapshotTs={data?.snapshot_ts ?? (status?.mode === 'replay' ? status.virtual_time : null)}
          stale={data?.stale ?? false}
          objectsReady={Boolean(data)}
          objectsError={Boolean(error)}
          onRetryObjects={() => setRetry(value => value + 1)}
          hideToolbar
        />
      </div>
      {mapControlsTarget && createPortal(
        <div aria-label="Выбор округа" className="sidebar-map-district-list" role="group">
          <button
            aria-pressed={!area}
            className={`sidebar-map-district-option${area ? '' : ' is-selected'}`}
            onClick={() => setArea(null)}
            type="button"
          >
            Все округа
          </button>
          {features.map(feature => {
            const code = feature.properties?.code ?? feature.properties?.name ?? feature.properties?.display_name
            if (!code) return null
            const selected = area === code
            return (
              <button
                aria-pressed={selected}
                className={`sidebar-map-district-option${selected ? ' is-selected' : ''}`}
                key={code}
                onClick={() => setArea(code)}
                type="button"
              >
                {code}
              </button>
            )
          })}
        </div>,
        mapControlsTarget,
      )}
      <MapControlsMenu label="Управление мониторингом">
        {(status?.mode === 'replay' || replayLoading || replayError) && (
          <section className="map-controls-section">
            <h2>Симуляция</h2>
            <ReplayControls />
          </section>
        )}
        <section className="map-controls-section">
          <h2>Объекты карты</h2>
          <label><input type="checkbox" checked={showAll} onChange={e => setShowAll(e.target.checked)} /> Показать все объекты</label>
          <small>Схематическое размещение объектов — не граница прав доступа.</small>
        </section>
      </MapControlsMenu>
    </section>
    <aside className="dashboard-info-panel">
      <DispatcherDashboardWidgets
        attentionSignals={latestAttentionSignals}
        attentionStatus={error ? 'error' : data ? 'ready' : 'loading'}
        onKpiData={setKpiData}
        onRetryAttention={() => setRetry(value => value + 1)}
      />
      {error && <p role="alert">{error} <button onClick={() => setRetry(v => v + 1)}>Повторить</button></p>}
    </aside>
  </div></div>
}
