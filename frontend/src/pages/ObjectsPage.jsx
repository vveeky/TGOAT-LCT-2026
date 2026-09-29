import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { getObjectCatalog } from '../api/objects'
import { getDashboard } from '../api/dashboard'
import { useReplayRuntime } from '../hooks/useReplayRuntime'
import { createSyntheticObjectLocations } from '../map/syntheticObjectLocations'
import { signalLabels } from '../api/signals'

function featureCode(feature) {
  return feature.properties?.code
    ?? feature.properties?.name
    ?? feature.properties?.display_name
    ?? 'Округ'
}

const areaOrder = new Intl.Collator('ru', { sensitivity: 'base' })

const riskLabels = {
  high: 'Высокий',
  low: 'Низкий',
  'no-data': 'Нет оценки',
}

export default function ObjectsPage() {
  const [params, setParams] = useSearchParams()
  const query = params.get('search') ?? ''
  const [objects, setObjects] = useState(null)
  const [dashboardObjects, setDashboardObjects] = useState([])
  const [error, setError] = useState(null)
  const [retry, setRetry] = useState(0)
  const [features, setFeatures] = useState([])
  const [areaError, setAreaError] = useState(false)
  const [area, setArea] = useState('')
  const [attention, setAttention] = useState('')
  const { status: replayStatus } = useReplayRuntime()

  useEffect(() => {
    let active = true
    fetch('/geo/moscow-okrugs.geojson')
      .then(response => {
        if (!response.ok) throw new Error(`GeoJSON HTTP ${response.status}`)
        return response.json()
      })
      .then(value => { if (active) setFeatures(value.features ?? []) })
      .catch(() => { if (active) setAreaError(true) })
    return () => { active = false }
  }, [])

  useEffect(() => {
    let active = true
    const refresh = () => Promise.all([getObjectCatalog(), getDashboard().catch(() => null)])
      .then(([value, dashboard]) => {
        if (active) {
          setObjects(value.items)
          setDashboardObjects(dashboard?.objects ?? [])
          setError(null)
        }
      })
      .catch(() => { if (active) setError('Не удалось загрузить объекты.') })
    refresh()
    const timer = setInterval(refresh, replayStatus?.mode === 'replay' ? 1500 : 30_000)
    return () => { active = false; clearInterval(timer) }
  }, [retry, replayStatus?.mode, replayStatus?.session_id])

  const areaNames = useMemo(() => new Map(features.map(feature => {
    const props = feature.properties ?? {}
    return [featureCode(feature), props.name ?? props.display_name ?? featureCode(feature)]
  })), [features])

  const mapped = useMemo(() => {
    const byId = new Map(createSyntheticObjectLocations(objects ?? [], features)
      .map(item => [item.object.id, item.featureCode]))
    const currentRisk = new Map(dashboardObjects.map(item => [item.id, item]))
    return (objects ?? []).map(item => ({
      ...item,
      area: byId.get(item.id) ?? '',
      displayRisk: item.operational_status !== 'available'
        ? 'no-data'
        : currentRisk.get(item.id)?.risk_level ?? (item.risk_flag === true ? 'high' : item.risk_flag === false ? 'low' : 'no-data'),
    }))
  }, [objects, features, dashboardObjects])

  const matching = mapped.filter(item => {
    const normalizedQuery = query.trim().toLocaleLowerCase('ru')
    const text = `${item.name} ${item.id} ${item.type ?? ''}`.toLocaleLowerCase('ru')
    if (normalizedQuery && !text.includes(normalizedQuery)) return false
    if (area && item.area !== area) return false
    if (attention === 'none' && item.human_state) return false
    if (attention === 'unavailable' && item.operational_status === 'available') return false
    if (attention && !['none', 'unavailable'].includes(attention) && item.human_state !== attention) return false
    return true
  })

  const visibleGroups = useMemo(() => {
    const groups = new Map()
    matching.forEach(item => {
      const key = item.area || 'unassigned'
      if (!groups.has(key)) groups.set(key, [])
      groups.get(key).push(item)
    })
    return [...groups.entries()]
      .sort(([left], [right]) => areaOrder.compare(left, right))
      .map(([code, groupObjects]) => ({
        code,
        name: areaNames.get(code) ?? (code === 'unassigned' ? 'Округ не указан' : code),
        objects: groupObjects,
      }))
  }, [matching, areaNames])

  const countsByArea = useMemo(() => mapped.reduce((counts, item) => {
    const code = item.area || 'unassigned'
    counts[code] = (counts[code] ?? 0) + 1
    return counts
  }, {}), [mapped])
  const areas = [...areaNames.entries()].sort(([left], [right]) => areaOrder.compare(left, right))
  if (countsByArea.unassigned) areas.push(['unassigned', 'Округ не указан'])
  const activeAttentionCount = mapped.filter(item => item.displayRisk === 'high').length
  const visibleCount = visibleGroups.reduce((count, group) => count + group.objects.length, 0)

  return (
    <section className="objects-page" aria-label="Каталог объектов">
      <header className="objects-page-header">
        <div>
          <span className="objects-page-kicker">Инфраструктура Москвы</span>
          <h1>Объекты по округам</h1>
          <p>
            Объекты мониторинга из backend API. Привязка к округам — условная схема для навигации.
          </p>
        </div>
        <div className="objects-page-summary" aria-label="Сводка объектов">
          <div><strong>{objects && !error ? mapped.length : '—'}</strong><span>объектов</span></div>
          <div><strong>{features.length || '—'}</strong><span>округов</span></div>
          <div className="is-attention"><strong>{objects && !error ? activeAttentionCount : '—'}</strong><span>требуют внимания</span></div>
        </div>
      </header>

      <div className="objects-directory">
        <aside className="objects-okrug-filter" aria-label="Административные округа">
          <button
            aria-pressed={!area}
            className={!area ? 'is-active' : ''}
            onClick={() => setArea('')}
            type="button"
          >
            <span>Все округа</span><strong>{objects && !error ? mapped.length : '—'}</strong>
          </button>
          {areas.map(([code]) => (
            <button
              aria-pressed={area === code}
              className={area === code ? 'is-active' : ''}
              key={code}
              onClick={() => setArea(area === code ? '' : code)}
              type="button"
            >
              <span>{code === 'unassigned' ? 'Без округа' : code}</span>
              <strong>{countsByArea[code] ?? 0}</strong>
            </button>
          ))}
        </aside>

        <section className="objects-directory-main" aria-label="Список объектов">
          <div className="objects-directory-toolbar">
            <label>
              <span className="sr-only">Поиск объектов</span>
              <input
                aria-label="Поиск по названию, ID или типу"
                onChange={event => setParams(event.target.value ? { search: event.target.value } : {}, { replace: true })}
                placeholder="Поиск по названию, ID или типу"
                type="search"
                value={query}
              />
            </label>
            <label className="objects-state-filter">
              <span className="sr-only">Состояние работы</span>
              <select aria-label="Состояние работы" onChange={event => setAttention(event.target.value)} value={attention}>
                <option value="">Все состояния</option>
                <option value="new">Требуют внимания</option>
                <option value="inspection_requested">Проверка запрошена</option>
                <option value="monitoring">Под наблюдением</option>
                <option value="none">Без активного сигнала</option>
                <option value="unavailable">Нет оценки</option>
              </select>
            </label>
            {objects && !error && <span>{visibleCount} объектов</span>}
          </div>

          {areaError && <p className="objects-page-state">Округа временно недоступны. Поиск объектов работает без них.</p>}
          {error && <div className="objects-page-state is-error" role="alert">{error} <button onClick={() => setRetry(value => value + 1)} type="button">Повторить</button></div>}
          {!objects && !error && <p className="objects-page-state" role="status">Загрузка объектов…</p>}
          {objects && !error && visibleGroups.map(group => (
            <section className="objects-okrug-group" key={group.code}>
              <header>
                <div><strong>{group.code === 'unassigned' ? 'Без округа' : group.code}</strong><span>{group.name}</span></div>
                <span>{group.objects.length}</span>
              </header>
              <div className="objects-table" role="list">
                {group.objects.map(item => {
                  const probability = item.operational_status === 'available' && Number.isFinite(item.risk_probability)
                    ? `${Math.round(item.risk_probability * 100)}%`
                    : null
                  const riskText = riskLabels[item.displayRisk]
                  const secondary = [
                    `ID ${item.id}`,
                    item.type ?? 'Без типа',
                    item.human_state ? signalLabels[item.human_state] : null,
                  ].filter(Boolean).join(' · ')
                  return (
                    <Link className="objects-table-row" key={item.id} role="listitem" to={`/objects/${item.id}`}>
                      <span className={`object-risk-dot is-${item.displayRisk}`} aria-hidden="true" />
                      <div className="objects-table-primary">
                        <strong>{item.name}</strong>
                        <span>{secondary}</span>
                      </div>
                      <div className="objects-table-metric" title={`Обновлено: ${item.updated_at ?? 'время неизвестно'}`}>
                        <strong>{item.channels_count ?? '—'}</strong><span>каналов</span>
                      </div>
                      <div className="objects-risk-cell">
                        <span aria-label={probability ? `${riskText}, риск ${probability}` : riskText} className={`objects-risk-label is-${item.displayRisk}`}>{riskText}</span>
                      </div>
                      <span className="objects-table-arrow" aria-hidden="true">→</span>
                    </Link>
                  )
                })}
                {!group.objects.length && <div className="objects-page-state">В этом округе объекты не найдены.</div>}
              </div>
            </section>
          ))}
          {objects && !error && !visibleGroups.length && <p className="objects-page-state">По выбранным условиям объекты не найдены.</p>}
        </section>
      </div>
    </section>
  )
}
