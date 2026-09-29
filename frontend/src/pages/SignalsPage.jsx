import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowUpRight } from 'lucide'
import { MorphIcon } from 'morphicons/react'
import { getSignals, riskText, signalLabels, WORKFLOW_UPDATED } from '../api/signals'
import { useReplayRuntime } from '../hooks/useReplayRuntime'
import { formatMoscowDateTime } from '../dateTime'

let savedSignalsView = 'grid'

function SignalsListIcon() {
  return <svg aria-hidden="true" fill="none" height="28" viewBox="0 0 32 32" width="28">
    <rect height="9" rx="4.5" stroke="currentColor" strokeWidth="2.4" width="26" x="3" y="4" />
    <rect height="9" rx="4.5" stroke="currentColor" strokeWidth="2.4" width="26" x="3" y="19" />
  </svg>
}

function SignalsGridIcon() {
  return <svg aria-hidden="true" fill="none" height="28" viewBox="0 0 32 32" width="28">
    <rect height="10" rx="3.2" stroke="currentColor" strokeWidth="2.4" width="10" x="3" y="3" />
    <rect height="10" rx="3.2" stroke="currentColor" strokeWidth="2.4" width="10" x="19" y="3" />
    <rect height="10" rx="3.2" stroke="currentColor" strokeWidth="2.4" width="10" x="3" y="19" />
    <rect height="10" rx="3.2" stroke="currentColor" strokeWidth="2.4" width="10" x="19" y="19" />
  </svg>
}

export default function SignalsPage() {
  const [filter, setFilter] = useState('new')
  const [offset, setOffset] = useState(0)
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [retry, setRetry] = useState(0)
  const [view, setView] = useState(() => {
    try {
      const stored = window.localStorage?.getItem('signals-view')
      if (stored === 'list' || stored === 'grid') savedSignalsView = stored
    } catch { /* Fall back to the current app session. */ }
    return savedSignalsView
  })
  const { status } = useReplayRuntime()
  useEffect(() => {
    let active = true
    const refresh = () => getSignals({ state: filter, limit: 50, offset }).then(value => {
      if (active) { setData(value); setError(null) }
    }).catch(e => { if (active) setError(e.status === 403 ? 'Раздел доступен диспетчерам.' : 'Не удалось загрузить сигналы. Повторите попытку.') })
    refresh()
    const timer = setInterval(refresh, status?.mode === 'replay' ? 1500 : 30000)
    window.addEventListener(WORKFLOW_UPDATED, refresh)
    return () => { active = false; clearInterval(timer); window.removeEventListener(WORKFLOW_UPDATED, refresh) }
  }, [filter, offset, retry, status?.mode])
  const chooseView = nextView => {
    savedSignalsView = nextView
    setView(nextView)
    try { window.localStorage?.setItem('signals-view', nextView) } catch { /* Keep the control usable when storage is unavailable. */ }
  }
  return <section className="content-page signals-page">
    <header className="content-page-heading signals-heading"><div><h1>Сигналы</h1><p>Открытые ситуации и решения диспетчера</p></div></header>
    <div className="signals-toolbar">
      <nav aria-label="Состояние сигнала" className="signals-filters">{Object.entries(signalLabels).map(([key, label]) =>
        <button key={key} type="button" aria-pressed={filter === key} onClick={() => { setFilter(key); setOffset(0); setData(null) }}><span>{key === 'new' ? 'Новые' : key === 'resolved' ? 'Решённые' : label}</span><strong>{data?.counts?.[key] ?? '—'}</strong></button>)}</nav>
      <div aria-label="Вид списка сигналов" className="signals-view-switch" role="group">
        <button aria-label="Показать списком" aria-pressed={view === 'list'} onClick={() => chooseView('list')} title="Список" type="button"><SignalsListIcon /></button>
        <button aria-label="Показать сеткой" aria-pressed={view === 'grid'} onClick={() => chooseView('grid')} title="Сетка" type="button"><SignalsGridIcon /></button>
      </div>
    </div>
    {error && <p className="signals-state is-error" role="alert">{error} <button onClick={() => setRetry(v => v + 1)}>Повторить</button></p>}
    {!data && !error && <p className="signals-state" role="status">Загружаем сигналы…</p>}
    {data && !error && data.total === 0 && <p className="signals-state">Нет ситуаций, требующих внимания по выбранным фильтрам.</p>}
    <ul className={`record-list signals-list is-${view}`}>{!error && data?.items.map(signal => <li className="record-card signal-card" key={signal.card_id}>
      <div className="signal-card-heading"><div><span className="signal-card-kicker">Объект</span><h2>{signal.object_name}</h2></div><span className={`state-pill state-${signal.state}`}>{signalLabels[signal.state]}</span></div>
      <div className="signal-card-meta"><div className="signal-risk"><span>Риск за сутки</span><strong aria-label={riskText(signal)}>{signal.assessment_status === 'available' && Number.isFinite(signal.risk_probability) ? `${Math.round(signal.risk_probability * 100)}%` : '—'}</strong></div><div className="signal-updated"><span>Обновлено</span><time>{formatMoscowDateTime(signal.last_model_update)}</time></div></div>
      <div className="signal-card-actions"><Link className="signal-open-link" to={`/objects/${signal.object_id}?signal=${encodeURIComponent(signal.card_id)}`}>Открыть сигнал <MorphIcon aria-hidden="true" icon={ArrowUpRight} size={15} strokeWidth={1.8} /></Link>
        {signal.active_request && <Link className="signal-request-link" to={`/maintenance?request_id=${signal.active_request.id}`}>Заявка №{signal.active_request.id}</Link>}
      </div>
    </li>)}</ul>
    {data && data.total > 50 && <nav aria-label="Страницы сигналов">
      <button disabled={offset === 0} onClick={() => { setOffset(Math.max(0, offset - 50)); setData(null) }}>Назад</button>
      <span>{offset + 1}–{Math.min(offset + 50, data.total)} из {data.total}</span>
      <button disabled={offset + 50 >= data.total} onClick={() => { setOffset(offset + 50); setData(null) }}>Далее</button>
    </nav>}
  </section>
}
