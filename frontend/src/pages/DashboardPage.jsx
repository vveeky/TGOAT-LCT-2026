import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { getDashboard } from '../api/dashboard'
import { getMaintenanceRequests } from '../api/maintenance'
import MoscowOkrugsMap from '../components/MoscowOkrugsMap'
import MapControlsMenu from '../components/MapControlsMenu'
import ReplayControls from '../components/ReplayControls'
import { useReplayRuntime } from '../hooks/useReplayRuntime'
import { formatMoscowDateTime, formatMoscowTime } from '../dateTime'
import { layoutPresets, normalizeWidgetLayout, moveWidget } from '../dashboard/widgetLayout'
import DashboardKpiRow from '../dashboard/DashboardKpiRow'
import { useAuth } from '../auth/useAuth'
import DispatcherMonitoringPage from './DispatcherMonitoringPage'
import RequestExecutionPage from './RequestExecutionPage'
import { hasRole, isDispatcher } from '../auth/permissions'

const emptyDistribution = {
  low: 0,
  medium: 0,
  high: 0,
  critical: 0,
}

const riskLabels = {
  critical: 'Критический',
  high: 'Высокий',
  medium: 'Средний',
  low: 'Норма',
  'no-data': 'Нет прогноза / недоступен',
}

const RISK_ORDER = ['critical', 'high', 'medium', 'low', 'no-data']
const WIDGETS = { status: 'Распределение рисков', attention: 'Требуют внимания', predictions: 'Последние прогнозы', activeRequests: 'Активные заявки' }
const WIDGET_STORAGE = 'tgoat.dashboard.functional-widgets.v1'

function readWidgets() {
  try {
    const saved = JSON.parse(localStorage.getItem(WIDGET_STORAGE))
    return {
      status: saved?.status ?? true,
      attention: saved?.attention ?? false,
      predictions: saved?.predictions ?? true,
      activeRequests: saved?.activeRequests ?? true,
    }
  } catch {
    return { status: true, attention: false, predictions: true, activeRequests: true }
  }
}

function buildRiskDonutSegments(summary, total) {
  if (!total) return []
  let offset = 0

  return RISK_ORDER.flatMap((key) => {
    const value = summary.find((risk) => risk.key === key)?.value ?? 0
    if (value <= 0) return []
    const sweep = (value / total) * 360
    const visibleSweep = Math.max(0, sweep - 1.5)
    const segment = {
      key,
      dashArray: `${visibleSweep} 360`,
      dashOffset: -offset,
    }
    offset += sweep
    return [segment]
  })
}

function RiskDonut({ segments, totalObjects, hasData }) {
  return (
    <svg
      aria-label={hasData ? `Всего объектов: ${totalObjects}` : 'Нет данных о рисках'}
      className="dashboard-risk-donut"
      role="img"
      viewBox="0 0 160 160"
    >
      <circle className="dashboard-risk-donut-track" cx="80" cy="80" pathLength="360" r="59.5" />
      <g transform="rotate(-90 80 80)">
        {segments.map((segment) => (
          <circle
            className={`dashboard-risk-sector is-${segment.key}`}
            cx="80"
            cy="80"
            key={segment.key}
            pathLength="360"
            r="59.5"
            strokeDasharray={segment.dashArray}
            strokeDashoffset={segment.dashOffset}
          />
        ))}
      </g>
      <text className="dashboard-risk-donut-total" textAnchor="middle" x="80" y="78">
        {hasData ? totalObjects : '—'}
      </text>
      <text className="dashboard-risk-donut-caption" textAnchor="middle" x="80" y="97">
        объектов
      </text>
    </svg>
  )
}

function formatPredictionMeta(prediction) {
  const score = Number.isFinite(prediction.risk_score)
    ? `${Math.round(prediction.risk_score * 100)}%`
    : '—'
  const from = prediction.forecast_from_hours ?? '—'
  const to = prediction.forecast_to_hours ?? '—'

  return `${prediction.risk_level?.toUpperCase() ?? '—'} · ${score} · ${from}–${to} ч`
}

export function DashboardPage() {
  const [selectedOkrug, setSelectedOkrug] = useState(null)
  const [dashboard, setDashboard] = useState(null)
  const [error, setError] = useState(null)
  const [retryKey, setRetryKey] = useState(0)
  const [requestCount, setRequestCount] = useState(null)
  const [requestError, setRequestError] = useState(null)
  useEffect(() => {
    let active = true
    const refresh = () => Promise.all(['draft', 'new', 'assigned', 'in_progress', 'awaiting_review'].map(status => getMaintenanceRequests({ status, limit: 1 })))
      .then(results => {
        if (!active) return
        setRequestCount(results.reduce((sum, result) => sum + result.total, 0))
        setRequestError(null)
      }).catch(error => {
        if (!active) return
        setRequestCount(null)
        setRequestError(error.message)
      })
    refresh()
    const interval = window.setInterval(refresh, 30_000)
    return () => { active = false; window.clearInterval(interval) }
  }, [retryKey])
  const [widgets, setWidgets] = useState(readWidgets)
  const [widgetLayout, setWidgetLayout] = useState(() => {
    try { return normalizeWidgetLayout(JSON.parse(localStorage.getItem('tgoat.dashboard.functional-layout.v1'))) }
    catch { return normalizeWidgetLayout(null) }
  })
  const saveLayout = (next) => {
    setWidgetLayout(next)
    try { localStorage.setItem('tgoat.dashboard.functional-layout.v1', JSON.stringify(next)) } catch { /* Session settings remain usable. */ }
  }
  const widgetProps = (id) => ({
    style: { order: widgetLayout.order.indexOf(id) },
    'data-widget': id,
    onDragOver: event => event.preventDefault(),
    onDrop: event => {
      event.preventDefault()
      saveLayout(moveWidget(widgetLayout, event.dataTransfer.getData('text/plain'), id))
    },
  })
  const widgetHandle = (id) => (
    <button type="button" className="widget-drag-handle" draggable
      aria-label={`Переместить виджет: ${WIDGETS[id]}`}
      onDragStart={event => event.dataTransfer.setData('text/plain', id)}
      onClick={() => {
        const index = widgetLayout.order.indexOf(id)
        if (index > 0) saveLayout(moveWidget(widgetLayout, id, widgetLayout.order[index - 1]))
      }}>⠿ <span>Переместить выше</span></button>
  )
  const toggleWidget = (key) => {
    const next = { ...widgets, [key]: !widgets[key] }
    setWidgets(next)
    try { localStorage.setItem(WIDGET_STORAGE, JSON.stringify(next)) } catch { /* Settings still work for this session. */ }
  }
  const {
    status: replayStatus,
    loading: replayLoading,
    error: replayError,
  } = useReplayRuntime()

  useEffect(() => {
    let active = true
    const refresh = () => getDashboard()
      .then((response) => {
        if (!active) return
        setDashboard(response)
        setError(null)
      })
      .catch((nextError) => { if (active) setError(nextError) })
    refresh()
    const interval = window.setInterval(refresh, replayStatus?.mode === 'replay' ? 1500 : 30_000)

    return () => {
      active = false
      window.clearInterval(interval)
    }
  }, [replayStatus?.mode, retryKey])

  const distribution = dashboard?.risk_distribution ?? emptyDistribution
  const totalObjects = dashboard?.objects_count ?? 0
  const attentionCount = (
    distribution.medium + distribution.high + distribution.critical
  )
  const riskSummary = RISK_ORDER.map((key) => ({
    key,
    label: riskLabels[key],
    value: key === 'no-data'
      ? (dashboard?.no_prediction_count ?? 0) + (dashboard?.degraded_count ?? 0)
      : distribution[key] ?? 0,
  }))
  const hasRiskData = riskSummary.some((risk) => risk.value > 0)
  const donutSegments = buildRiskDonutSegments(
    riskSummary,
    riskSummary.reduce((sum, risk) => sum + risk.value, 0),
  )

  const topObjects = useMemo(
    () => dashboard?.top_objects ?? [],
    [dashboard]
  )
  const latestPredictions = dashboard?.recent_predictions ?? []
  const objectNames = useMemo(() => (
    new Map((dashboard?.objects ?? []).map((object) => [object.id, object.name]))
  ), [dashboard])

  return (
    <div className="dashboard-page">
      <div className="dashboard-workspace">
        <section
          className="dashboard-map-region"
          aria-label="Карта административных округов Москвы"
        >
          <DashboardKpiRow
            objectCount={dashboard ? totalObjects : null}
            activeRequestsCount={requestCount}
            snapshotKey={dashboard?.snapshot_ts}
          />
          <div className="dashboard-map-layer">
            <MoscowOkrugsMap
              onSelectionChange={setSelectedOkrug}
              objects={dashboard?.objects ?? []}
              snapshotTs={dashboard?.snapshot_ts ?? null}
              stale={dashboard?.stale ?? true}
              replayMode={replayStatus?.mode === 'replay'}
              objectsReady={Boolean(dashboard)}
              objectsError={Boolean(error)}
              onRetryObjects={() => setRetryKey((key) => key + 1)}
            />
          </div>
          {(replayStatus?.mode === 'replay' || replayLoading || replayError) && (
            <MapControlsMenu label="Управление симуляцией">
              <section className="map-controls-section">
                <h2>Симуляция</h2>
                <ReplayControls />
              </section>
            </MapControlsMenu>
          )}
        </section>

        <aside
          className="dashboard-info-panel"
          aria-label="Сводка по районам и объектам"
        >
          <div className="dashboard-panel-heading">
            <div>
              <span className="dashboard-panel-eyebrow">
                Москва · мониторинг
              </span>

              <h1>
                Состояние
                <br />
                системы
              </h1>

              <p>Контролируемая инфраструктура города</p>
            </div>
          </div>

          {error && (
            <div className="dashboard-api-error" role="alert">
              Не удалось загрузить сводку: {error.message}
              <button type="button" onClick={() => setRetryKey((key) => key + 1)}>Повторить</button>
            </div>
          )}

          <details className="dashboard-widget-settings">
            <summary>Настроить виджеты</summary>
            <label>Раскладка <select value={widgetLayout.preset} onChange={event => saveLayout({ ...widgetLayout, preset: event.target.value })}>
              {Object.entries(layoutPresets).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select></label>
            {Object.entries(WIDGETS).map(([key, label]) => (
              <label key={key}><input type="checkbox" checked={widgets[key]} onChange={() => toggleWidget(key)} /> {label}</label>
            ))}
            <button type="button" onClick={() => {
              saveLayout(normalizeWidgetLayout(null))
              const next = Object.fromEntries(Object.keys(WIDGETS).map(key => [key, true]))
              setWidgets(next)
              try { localStorage.setItem(WIDGET_STORAGE, JSON.stringify(next)) } catch { /* Session remains usable. */ }
            }}>Сбросить настройки виджетов</button>
          </details>

          <div className="dashboard-map-context">
            <span>Область на карте</span>
            <strong>{selectedOkrug ?? 'Все округа'}</strong>
          </div>

          {dashboard && (
            <p className={`dashboard-data-freshness${dashboard.stale ? ' is-stale' : ''}`} role="status">
              {dashboard.stale ? 'Исторический снимок' : `Статус: ${dashboard.cycle_status}`}
              {' · '}{formatMoscowDateTime(dashboard.snapshot_ts)}
            </p>
          )}

          <div className="system-overview-main">
            <div className="system-overview-card">
              <span>Объектов</span>
              <strong>{dashboard ? totalObjects : '—'}</strong>
              <small>под наблюдением</small>
            </div>

            <div className="system-overview-card system-overview-risk">
              <span>Требуют внимания</span>
              <strong>{dashboard ? attentionCount : '—'}</strong>
              <small>активные прогнозы</small>
            </div>
          </div>

          <div className="system-kpi-list">
            <div className="system-kpi-row">
              <div>
                <i className="kpi-dot critical" />
                <span>Критический риск</span>
              </div>
              <strong>{dashboard ? dashboard.critical_risk_count : '—'}</strong>
            </div>

            <div className="system-kpi-row">
              <div>
                <i className="kpi-dot high" />
                <span>Высокий риск</span>
              </div>
              <strong>{dashboard ? dashboard.high_risk_count : '—'}</strong>
            </div>

            <div className="system-kpi-row">
              <div>
                <i className="kpi-dot medium" />
                <span>Средний риск</span>
              </div>
              <strong>{dashboard ? distribution.medium : '—'}</strong>
            </div>

            <div className="system-kpi-row">
              <div>
                <i className="kpi-dot request" />
                <span>Нет прогноза / недоступен</span>
              </div>
              <strong>{dashboard ? dashboard.no_prediction_count + dashboard.degraded_count : '—'}</strong>
            </div>
          </div>

          <div className={`dashboard-configurable-widgets layout-${widgetLayout.preset}`}>
          {widgets.status && <section className="info-section risk-distribution-section" {...widgetProps('status')}>
            {widgetHandle('status')}
            <div className="info-section-heading">
              <span>01</span>
              <h2 className="info-section-title">Распределение рисков</h2>
            </div>
            <div className="dashboard-risk-summary-grid">
              <RiskDonut
                hasData={Boolean(dashboard && hasRiskData)}
                segments={donutSegments}
                totalObjects={totalObjects}
              />
              <div className="dashboard-risk-breakdown">
                <span className="dashboard-risk-breakdown-title">Объекты по статусу</span>
                <div className="dashboard-risk-breakdown-list" role="list">
                  {riskSummary.map((risk) => (
                    <div className="dashboard-risk-breakdown-row" key={risk.key} role="listitem">
                      <i className={`dashboard-risk-dot is-${risk.key}`} aria-hidden="true" />
                      <span>{risk.label}</span>
                      <strong>{dashboard ? risk.value : '—'}</strong>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </section>}

          {widgets.attention && <section className="info-section" {...widgetProps('attention')}>
            {widgetHandle('attention')}
            <div className="info-section-heading">
              <span>02</span>
              <h2 className="info-section-title">Требуют внимания</h2>
            </div>

            <div className="attention-list">
              {topObjects.slice(0, 3).map((object) => (
                <Link
                  className="attention-item dashboard-attention-item"
                  key={object.id}
                  to={`/objects/${object.id}`}
                >
                  <div className="attention-item-info">
                    <strong>{object.name}</strong>
                    <span>
                      {object.channels_count} каналов · {object.risk_level ?? '—'}
                    </span>
                  </div>

                  <div className="attention-score">
                    {Number.isFinite(object.risk_score)
                      ? `${Math.round(object.risk_score * 100)}%`
                      : '—'}
                  </div>
                </Link>
              ))}

              {dashboard && !topObjects.length && (
                <div className="dashboard-empty-state">
                  Нет объектов с активным прогнозом
                </div>
              )}
            </div>
          </section>}

          {widgets.predictions && <section className="info-section" {...widgetProps('predictions')}>
            {widgetHandle('predictions')}
            <div className="info-section-heading">
              <span>03</span>
              <h2 className="info-section-title">Последние прогнозы</h2>
            </div>

            <div className="prediction-mini">
              {latestPredictions.slice(0, 3).map((prediction) => (
                <Link
                  className="prediction-mini-item"
                  key={prediction.id}
                  to={`/objects/${prediction.object_id}`}
                >
                  <span className="prediction-time">
                    {formatMoscowTime(prediction.created_at)}
                  </span>

                  <div>
                    <strong>
                      {objectNames.get(prediction.object_id)
                        ?? `Объект ${prediction.object_id}`}
                    </strong>
                    <small>{formatPredictionMeta(prediction)}</small>
                  </div>
                </Link>
              ))}

              {dashboard && !latestPredictions.length && (
                <div className="dashboard-empty-state">
                  Нет последних прогнозов
                </div>
              )}
            </div>
          </section>}
          {widgets.activeRequests && <section className="info-section" {...widgetProps('activeRequests')}>
            {widgetHandle('activeRequests')}
            <h2 className="info-section-title">Активные заявки</h2>
            <Link to="/maintenance">{requestCount ?? '—'} · Открыть заявки</Link>
            {requestError && <p role="alert">Не удалось загрузить заявки: {requestError}<button type="button" onClick={() => setRetryKey(key => key + 1)}>Повторить</button></p>}
          </section>}
          </div>
        </aside>
      </div>
    </div>
  )
}

export default function RoleDashboardPage({ mapControlsTarget }) {
  const { user } = useAuth()
  return isDispatcher(user) ? <DispatcherMonitoringPage mapControlsTarget={mapControlsTarget} />
    : hasRole(user, 'technician') ? <RequestExecutionPage /> : <DashboardPage />
}
