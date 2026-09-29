import { useEffect, useState } from 'react'

const HISTORY_KEY = 'tgoat.dashboard.kpi-history.v1'
const WIDTH = 144
const HEIGHT = 40
const PADDING = 4
const SAMPLE_INTERVAL = 15_000

function isCount(value) {
  return value !== null && value !== undefined && Number.isFinite(Number(value))
}

function readHistory() {
  try {
    const value = JSON.parse(window.sessionStorage.getItem(HISTORY_KEY))
    return {
      objects: Array.isArray(value?.objects) ? value.objects : [],
      activeRequests: Array.isArray(value?.activeRequests) ? value.activeRequests : [],
    }
  } catch {
    return { objects: [], activeRequests: [] }
  }
}

function makePath(samples, currentValue) {
  const values = samples.map(({ value }) => Number(value)).filter(Number.isFinite)
  if (values.length < 2) {
    return isCount(currentValue)
      ? `M${PADDING} ${HEIGHT / 2} H${WIDTH - PADDING}`
      : ''
  }

  const minimum = Math.min(...values)
  const maximum = Math.max(...values)
  const range = maximum - minimum
  const points = values.map((value, index) => ({
    x: PADDING + (index / (values.length - 1)) * (WIDTH - PADDING * 2),
    y: range === 0 ? HEIGHT / 2 : HEIGHT - PADDING - ((value - minimum) / range) * (HEIGHT - PADDING * 2),
  }))
  const path = points.reduce((result, point, index) => {
    if (index === 0) return `M${point.x.toFixed(2)} ${point.y.toFixed(2)}`
    const previous = points[index - 1]
    const middleX = (previous.x + point.x) / 2
    const middleY = (previous.y + point.y) / 2
    return `${result} Q${middleX.toFixed(2)} ${previous.y.toFixed(2)} ${middleX.toFixed(2)} ${middleY.toFixed(2)} T${point.x.toFixed(2)} ${point.y.toFixed(2)}`
  }, '')
  return path
}

function DashboardKpiSparkline({ id, label, samples, value }) {
  if (samples.length < 2) return null
  const path = makePath(samples, value)
  const description = `${label}: наблюдаемые значения показателя`

  return (
    <svg
      aria-label={description}
      className={`dashboard-kpi-sparkline is-${id}`}
      fill="none"
      preserveAspectRatio="none"
      role="img"
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
    >
      <title>{description}</title>
      {path && <path d={path} />}
    </svg>
  )
}

function formatCount(value) {
  return new Intl.NumberFormat('ru-RU').format(Number(value))
}

function summarize(samples, currentValue) {
  if (!isCount(currentValue) || samples.length < 2) return { label: null, direction: 'neutral' }

  const first = Number(samples[0].value)
  const current = Number(currentValue)
  if (first === 0 || first === current) return { label: null, direction: 'neutral' }

  const change = ((current - first) / first) * 100
  const label = `${change > 0 ? '+' : ''}${new Intl.NumberFormat('ru-RU', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(change)}%`
  return { label, direction: change > 0 ? 'up' : change < 0 ? 'down' : 'neutral' }
}

export default function DashboardKpiRow({ objectCount, activeRequestsCount, snapshotKey }) {
  const [history, setHistory] = useState(readHistory)

  useEffect(() => {
    const captureSample = () => {
      const capturedAt = Date.now()
      setHistory(current => {
        let changed = false
        const next = { ...current }
        for (const [key, value] of [['objects', objectCount], ['activeRequests', activeRequestsCount]]) {
          if (!isCount(value)) continue
          const samples = current[key]
          const previous = samples.at(-1)
          const count = Number(value)
          if (!previous || previous.value !== count || capturedAt - previous.at >= SAMPLE_INTERVAL) {
            next[key] = [...samples, { at: capturedAt, value: count }].slice(-48)
            changed = true
          }
        }
        if (changed) {
          try { window.sessionStorage.setItem(HISTORY_KEY, JSON.stringify(next)) } catch { /* In-memory chart remains available. */ }
        }
        return changed ? next : current
      })
    }
    captureSample()
    const timer = window.setInterval(captureSample, SAMPLE_INTERVAL)
    return () => window.clearInterval(timer)
  }, [objectCount, activeRequestsCount, snapshotKey])

  const kpis = [
    {
      id: 'objects',
      label: 'Всего объектов',
      value: objectCount,
      samples: history.objects,
    },
    {
      id: 'active-requests',
      label: 'Активные заявки',
      value: activeRequestsCount,
      samples: history.activeRequests,
    },
  ]

  return (
    <section className="dashboard-kpi-grid" aria-label="Ключевые показатели">
      {kpis.map(({ id, label, value, samples }) => {
        const trend = summarize(samples, value)
        return (
          <article
            aria-label={[
              `${label}: ${isCount(value) ? formatCount(value) : 'нет данных'}`,
              trend.label ? `изменение ${trend.label}` : null,
            ].filter(Boolean).join('; ')}
            className="dashboard-kpi-card"
            key={id}
          >
            <div className="dashboard-kpi-heading">
              <h2 className="dashboard-kpi-label">{label}</h2>
              <DashboardKpiSparkline
                id={id}
                label={label}
                samples={samples}
                value={value}
              />
            </div>
            <div className="dashboard-kpi-value">
              <strong>{isCount(value) ? formatCount(value) : '—'}</strong>
              {trend.label && <div className="dashboard-kpi-footer">
                <span className={`dashboard-kpi-change is-${trend.direction}`}>{trend.label}</span>
              </div>}
            </div>
          </article>
        )
      })}
    </section>
  )
}
