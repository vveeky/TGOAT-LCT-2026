import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { HexGrid, Hexagon, Layout } from 'react-hexgrid'
import { getObject, getObjectChannels } from '../api/objects'
import { getOperationalObject, getOperationalRiskHistory } from '../api/operational'
import { getSignals, signalLabels, decisionLabels, WORKFLOW_UPDATED } from '../api/signals'
import SignalDecision from '../components/SignalDecision'
import { useReplayRuntime } from '../hooks/useReplayRuntime'
import { formatMoscowDateTime } from '../dateTime'
import { mergeSignalContext } from '../dashboard/mergeSignalContext'
import { useAuth } from '../auth/useAuth'
import { isDispatcher } from '../auth/permissions'
import { getMaintenanceRequests, getMaintenanceRequest } from '../api/maintenance'
import { getUserHistory } from '../api/operational'
import PlannedWorkPanel from '../components/PlannedWorkPanel'

const activityLabels={signal_created:'Сигнал появился',created:'Заявка создана',submitted:'Заявка отправлена',assigned:'Исполнитель назначен',started:'Проверка начата',result_submitted:'Результат проверки отправлен',accepted:'Результат принят',returned:'Проверка возвращена на доработку',cancelled:'Заявка отменена'}
const GRID_COLUMNS = 46
const HEX_SIZE = 1.75
const HEX_SPACING = 1.1

const channelRiskLabels = {
  'no-data': 'Оценка риска недоступна',
  normal: 'Низкая оценка риска',
  medium: 'Средняя оценка риска',
  high: 'Высокая оценка риска',
  critical: 'Критическая оценка риска',
}

const channelSortCollator = new Intl.Collator('ru', { numeric: true, sensitivity: 'base' })

function sortChannels(channels) {
  return [...channels].sort((left, right) => {
    for (const field of ['system_type', 'system_tag', 'sensor_type']) {
      const result = channelSortCollator.compare(String(left?.[field] ?? ''), String(right?.[field] ?? ''))
      if (result !== 0) return result
    }
    return channelSortCollator.compare(
      String(left?.channel_id ?? left?.id ?? ''),
      String(right?.channel_id ?? right?.id ?? ''),
    )
  })
}

function getChannelRisk(channel) {
  if (['low', 'medium', 'high', 'critical'].includes(channel.risk_level)) {
    return channel.risk_level === 'low' ? 'normal' : channel.risk_level
  }
  if (channel.latest_alarm === true) return 'high'
  if (channel.latest_value != null && channel.latest_alarm === false) return 'normal'
  return 'no-data'
}

function channelRequiresAttention(channel) {
  return channel.latest_alarm === true || ['medium', 'high', 'critical'].includes(getChannelRisk(channel))
}

function latestChannelState(channel) {
  if (channel.latest_alarm === true) return 'Тревога'
  if (channel.latest_value == null) return 'Нет актуального значения'
  return 'Норма'
}

function channelDataAge(timestamp, referenceTimestamp) {
  const eventTime = Date.parse(timestamp ?? '')
  const referenceTime = Date.parse(referenceTimestamp ?? new Date().toISOString())
  if (!Number.isFinite(eventTime) || !Number.isFinite(referenceTime)) return 'Неизвестна'
  const hours = Math.max(0, Math.floor((referenceTime - eventTime) / 3_600_000))
  const days = Math.floor(hours / 24)
  const remainingHours = hours % 24
  return days ? `${days} дн. ${remainingHours} ч.` : `${hours} ч.`
}

function getHexCoordinates(index, columns = GRID_COLUMNS) {
  const row = Math.floor(index / columns)
  const column = index % columns
  const q = column
  const r = row - Math.floor(column / 2)
  return { q, r, s: -q - r }
}

function ChannelHoneycomb({ channels, selectedId, onSelect }) {
  const columns = Math.min(GRID_COLUMNS, Math.max(1, Math.ceil(Math.sqrt(channels.length * 2))))
  const hexSize = Math.min(HEX_SIZE * (GRID_COLUMNS / columns), 12)
  const rows = Math.max(1, Math.ceil(channels.length / columns))
  const columnStep = hexSize * 1.5 * HEX_SPACING
  const rowStep = hexSize * Math.sqrt(3) * HEX_SPACING
  const originX = -((columns - 1) * columnStep) / 2
  const originY = -(((rows - 1) * rowStep) + rowStep / 2) / 2
  const viewWidth = (columns - 1) * columnStep + hexSize * 3
  const viewHeight = (rows - 1) * rowStep + hexSize * 3

  return <div className="channel-grid-stage">
    <HexGrid width="100%" height="100%" viewBox={`${-viewWidth / 2} ${-viewHeight / 2} ${viewWidth} ${viewHeight}`}>
      <Layout flat origin={{ x: originX, y: originY }} size={{ x: hexSize, y: hexSize }} spacing={HEX_SPACING}>
        {channels.map((channel, index) => {
          const risk = getChannelRisk(channel)
          const selected = String(selectedId) === String(channel.id)
          return <Hexagon {...getHexCoordinates(index, columns)} className={`channel-risk-hex is-${risk}${selected ? ' is-selected' : ''}`} key={channel.id} style={{ '--hex-order': index }} onClick={() => onSelect(channel.id)} />
        })}
      </Layout>
    </HexGrid>
  </div>
}

function signalRisk(signal) {
  return signal?.assessment_status === 'available' && Number.isFinite(signal.risk_probability)
    ? `${Math.round(signal.risk_probability * 100)}%`
    : 'Оценка временно недоступна'
}

export default function DispatcherObjectPage() {
  const { id } = useParams()
  const {user}=useAuth(),dispatcher=isDispatcher(user)
  const [params] = useSearchParams()
  const preferred = params.get('signal')
  const requestId=params.get('request_id')
  const [taskContext,setTaskContext]=useState(null)
  useEffect(()=>{let active=true;if(requestId)getMaintenanceRequest(requestId).then(item=>{if(active)setTaskContext({key:requestId,item})}).catch(()=>{if(active)setTaskContext({key:requestId,error:true})});return()=>{active=false}},[requestId])
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [retry, setRetry] = useState(0)
  const [selectedChannelId, setSelectedChannelId] = useState(null)
  const [onlyAttention, setOnlyAttention] = useState(false)
  const { status } = useReplayRuntime()
  useEffect(() => {
    let active = true
    const refresh = async () => {
      try {
        const object = await getObject(id)
        const [signals, risk, channels, history, tasks, operational] = await Promise.allSettled([
          dispatcher?getSignals({ object_id: id, limit: 500 }):Promise.resolve({items:[]}), getOperationalObject(id), getObjectChannels(id), getOperationalRiskHistory(id,30),
          getMaintenanceRequests({object_id:id,limit:100}),getUserHistory({object_id:id,limit:20}),
        ])
        if (active) {
          setData(previous => {
            const session = status?.session_id ?? 'normal'
            const merged = signals.status === 'fulfilled' && previous?.id === id && previous.session === session
              ? { ...signals, value: { ...signals.value, items: mergeSignalContext(signals.value.items, previous.signals.value?.items) } }
              : signals
            return { id, session, object, signals: merged, risk, channels, history,tasks,operational }
          })
          setError(null)
        }
      } catch (e) { if (active) setError(e.status === 404 || e.status === 403 ? 'Объект не найден или недоступен в вашей области доступа.' : 'Не удалось загрузить объект.') }
    }
    refresh()
    const timer = setInterval(refresh, status?.mode === 'replay' ? 1500 : 30000)
    window.addEventListener(WORKFLOW_UPDATED, refresh)
    return () => { active = false; clearInterval(timer); window.removeEventListener(WORKFLOW_UPDATED, refresh) }
  }, [id, retry, status?.mode, status?.session_id,dispatcher])
  const current = data?.id === id && data.session === (status?.session_id??'normal') ? data : null
  const signals = current?.signals.value?.items ?? []
  const signal = signals.find(s => s.card_id === preferred) ?? signals.find(s => s.state !== 'resolved') ?? signals[0]
  const relatedTasks = (current?.tasks.value?.items ?? []).filter(task => (
    String(task.id) !== String(signal?.active_request?.id)
  ))
  const risk = current?.risk.value
  const objectChannelCount = current?.object.channels_count ?? current?.channels.value?.items?.length ?? '—'
  const openSignalCount = current?.signals.status === 'fulfilled'
    ? signals.filter(item => item.state !== 'resolved').length
    : '—'
  const relatedRequestCount = current?.tasks.status === 'fulfilled'
    ? current.tasks.value.items.length
    : '—'
  const channels = sortChannels(current?.channels.value?.items ?? [])
  const attentionChannels = channels.filter(channelRequiresAttention)
  const visibleChannels = onlyAttention ? attentionChannels : channels
  const selectedChannel = channels.find(channel => String(channel.id) === String(selectedChannelId)) ?? null
  const referenceTime = status?.mode === 'replay' ? status.virtual_time : null
  const channelCounts = channels.reduce((counts, channel) => {
    counts[getChannelRisk(channel)] += 1
    return counts
  }, { 'no-data': 0, normal: 0, medium: 0, high: 0, critical: 0 })
  return (
    <section className="content-page object-page">
      <div className="object-monitor-main">
        {error && <p role="alert">{error} <button onClick={() => setRetry(v => v + 1)}>Повторить</button></p>}
        {!current && !error && <p role="status">Загружаем объект…</p>}
        {current && !error && <>
          <header className="content-page-heading object-monitor-header">
            <div className="object-monitor-title">
              <nav className="object-monitor-breadcrumb" aria-label="Навигация"><Link to="/objects">Объекты</Link><span>/</span><span aria-current="page">{current.object.name}</span></nav>
              <span className="page-eyebrow">Мониторинг датчиков</span>
              <h1>{current.object.name}</h1>
              <p>Регулярная honeycomb-сетка всех каналов выбранного объекта.</p>
            </div>
            <div className="object-monitor-summary" aria-label="Сводка объекта">
              <div><span>Каналов</span><strong>{objectChannelCount}</strong></div>
              <div><span>Внимание</span><strong>{attentionChannels.length}</strong></div>
              <div><span>Сигналы</span><strong>{openSignalCount}</strong></div>
              <div><span>Заявки</span><strong>{relatedRequestCount}</strong></div>
            </div>
          </header>

          <section className="content-panel object-detail-section object-channels-section">
            <div className="object-monitor-grid-heading"><span>Каналы · honeycomb</span><small>{visibleChannels.length} из {channels.length} · каждая сота — канал</small></div>
            {channels.length > 0 && <div className="object-channel-toolbar">
              <label><input checked={onlyAttention} onChange={event => setOnlyAttention(event.target.checked)} type="checkbox" />Только требующие внимания <strong>{attentionChannels.length}</strong></label>
            </div>}
            <div className="object-monitor-grid-area">
              {current.channels.status === 'rejected' ? <div className="object-monitor-state" role="alert">Не удалось загрузить каналы.</div> : channels.length === 0 ? <div className="object-monitor-state">Каналы объекта не найдены.</div> : visibleChannels.length === 0 ? <div className="object-monitor-state">Каналов, требующих внимания, нет.</div> : <ChannelHoneycomb channels={visibleChannels} selectedId={selectedChannelId} onSelect={setSelectedChannelId} />}
            </div>
            {channels.length > 0 && <>
              <ol aria-label="Список каналов" className="sr-only">
                {visibleChannels.map(channel => {
                  const state = latestChannelState(channel)
                  return <li className="object-channel-row" key={channel.id}>
                    <button className="sr-only" onClick={() => setSelectedChannelId(channel.id)} type="button">
                      <strong>{channel.sensor_type || channel.name || `Канал ${channel.id}`}</strong>
                      <span>{state}</span>
                    </button>
                  </li>
                })}
              </ol>
            </>}
            <span className="sr-only">Открыто каналов: {channels.length}. Сигнал тревоги не подтверждает аварию.</span>
          </section>

          <section className="content-panel object-detail-section object-events-section">
            <div className="content-panel-heading"><div><span className="page-eyebrow">Сводка телеметрии</span><h2>Последние зарегистрированные события</h2></div><span className="inline-status">До 10 записей</span></div>
            <p className="section-description">Последнее наблюдение каждого канала. Полная история доступна на странице канала.</p>
            {current.channels.status === 'rejected' ? <p role="alert">События временно недоступны.</p> : channels.filter(channel => channel.last_event_at).length === 0 ? <p className="page-state page-state-empty">Зарегистрированных событий пока нет.</p> : <ol className="event-timeline object-event-list">{channels.filter(channel => channel.last_event_at).toSorted((a, b) => new Date(b.last_event_at) - new Date(a.last_event_at)).slice(0, 10).map(channel => <li className="event-record object-event-row" key={channel.id}><time>{formatMoscowDateTime(channel.last_event_at)}</time><Link to={`/channels/${channel.id}`}>{channel.sensor_type || channel.name || 'Канал'}</Link><span>{channel.latest_value ?? 'Значение отсутствует'}</span>{channel.latest_alarm === true && <span className="state-pill state-critical">Тревога</span>}</li>)}</ol>}
          </section>

          <section className="content-panel object-detail-section object-risk-history-section">
            <div className="content-panel-heading"><div><span className="page-eyebrow">История оценок</span><h2>Динамика оценки риска</h2></div><Link to={`/predictions?object_id=${id}`}>Вся история →</Link></div>
            {current.history.status === 'rejected' ? <p role="alert">История временно недоступна.</p> : current.history.value.length === 0 ? <p className="page-state page-state-empty">История оценок пока пуста.</p> : <ol className="risk-history-list">{current.history.value.toSorted((a, b) => new Date(b.snapshot_ts) - new Date(a.snapshot_ts)).slice(0, 8).map((item, index) => { const value = item.prediction_status === 'available' && Number.isFinite(item.risk_probability) ? item.risk_probability : null; return <li className={`risk-history-row${index === 0 ? ' is-latest' : ''}`} key={item.identity}><time>{formatMoscowDateTime(item.snapshot_ts)}</time><span className="risk-history-value">{value == null ? 'Недоступна' : `${Math.round(value * 100)}%`}</span><span className="risk-history-meter" aria-hidden="true"><span style={{ width: value == null ? '0%' : `${Math.round(value * 100)}%` }} /></span><Link to={`/predictions/${encodeURIComponent(item.identity)}`}>Расчёт →</Link></li> })}</ol>}
          </section>

          <details className="content-panel object-technical-details"><summary>Технические сведения</summary><pre>{JSON.stringify({ prediction_identity: risk?.prediction_identity, status: risk?.status, snapshot_ts: risk?.snapshot_ts, model_version: risk?.model_version, feature_schema_version: risk?.feature_schema_version }, null, 2)}</pre></details>
        </>}
      </div>

      <aside className="object-monitor-panel">
        {current && !error && <>
          <section className={`object-overview-panel${selectedChannel ? ' has-selected-channel' : ''}`}>
            {selectedChannel ? <>
              <button className="object-monitor-back" type="button" onClick={() => setSelectedChannelId(null)}>← Все каналы</button>
              <span className="object-monitor-panel-kicker">Выбранный канал</span>
              <h2 className="object-monitor-panel-name">{selectedChannel.sensor_type || selectedChannel.name || `Канал ${selectedChannel.id}`}</h2>
              <span className={`channel-risk-badge is-${getChannelRisk(selectedChannel)}`}>{selectedChannel.latest_alarm === true ? 'Тревога в последнем событии' : channelRiskLabels[getChannelRisk(selectedChannel)]}</span>
              <dl className="channel-detail-list">
                <div><dt>ID</dt><dd>{selectedChannel.id}</dd></div>
                <div><dt>Тип датчика</dt><dd>{selectedChannel.sensor_type || '—'}</dd></div>
                <div><dt>Последнее состояние датчика</dt><dd>{latestChannelState(selectedChannel)}</dd></div>
                {selectedChannel.latest_value != null && String(selectedChannel.latest_value) !== latestChannelState(selectedChannel) && <div><dt>Последнее значение</dt><dd>{selectedChannel.latest_value}</dd></div>}
                <div><dt>Последнее событие</dt><dd>{selectedChannel.last_event_at ? formatMoscowDateTime(selectedChannel.last_event_at) : 'Нет данных'}</dd></div>
                <div><dt>Давность данных</dt><dd>{channelDataAge(selectedChannel.last_event_at, referenceTime)}</dd></div>
                <div><dt>Прогнозный риск ML</dt><dd>{selectedChannel.risk_score == null ? 'Нет оценки' : `${channelRiskLabels[getChannelRisk(selectedChannel)]} · ${Math.round(selectedChannel.risk_score * 100)}%`}</dd></div>
              </dl>
              <Link className="object-monitor-open-channel" to={`/channels/${selectedChannel.id}`}>Открыть канал <span>→</span></Link>
            </> : <>
              <span className="object-monitor-panel-kicker">Общее состояние</span>
              <h2 className="object-monitor-panel-name">{current.object.name}</h2>
              <p>Каналы объекта и последние доступные данные.</p>
              <dl className="channel-detail-list is-summary">
                <div><dt>Всего</dt><dd>{channels.length}</dd></div>
                <div><dt>Без тревоги</dt><dd>{channelCounts.normal}</dd></div>
                <div><dt>Нет данных</dt><dd>{channelCounts['no-data']}</dd></div>
                <div><dt>Средний уровень</dt><dd>{channelCounts.medium}</dd></div>
                <div><dt>Высокий уровень</dt><dd>{channelCounts.high}</dd></div>
                <div><dt>Критический</dt><dd>{channelCounts.critical}</dd></div>
              </dl>
              <div className="channel-grid-legend" aria-label="Состояния каналов">
                {Object.entries(channelRiskLabels).map(([level, label]) => <span key={level}><i className={`is-${level}`} />{label}</span>)}
              </div>
            </>}
          </section>
          <section className="content-panel object-current-assessment" aria-labelledby="current-assessment-title">
            <div className="content-panel-heading"><div><span className="page-eyebrow">Состояние сейчас</span><h2 id="current-assessment-title">Текущая оценка</h2></div><span className={`state-pill ${risk?.status === 'available' ? 'state-available' : 'state-degraded'}`}>{risk?.status === 'available' ? 'Оценка доступна' : 'Оценка временно недоступна'}</span></div>
            {risk?.status === 'available' && Number.isFinite(risk.risk_probability) ? <p className="object-current-score">{Math.round(risk.risk_probability * 100)}%</p> : <p className="object-current-score is-unavailable">—</p>}
            <p className="object-score-caption">Риск тревожного события в ближайшие сутки</p>
            <p className="object-updated-at">Обновлено: {risk?.snapshot_ts ? formatMoscowDateTime(risk.snapshot_ts) : 'время недоступно'}</p>
            {(!risk || risk.status !== 'available') && <p className="inline-note">Недостаточно доступных данных для текущей оценки. Это не означает отсутствие риска.</p>}
          </section>
          <Link className="object-create-request" to={`/maintenance?create=1&object_id=${id}`}>Создать заявку вручную <span aria-hidden="true">→</span></Link>
          {requestId && <section className="content-panel object-request-basis"><h2>Основание выбранной заявки</h2>{taskContext?.key !== requestId ? <p role="status">Загружаем основание проверки…</p> : taskContext.error || String(taskContext.item.object_id) !== id ? <p role="alert">Заявка недоступна или относится к другому объекту.</p> : <><Link to={`${dispatcher ? '/maintenance' : '/tasks'}?request_id=${requestId}`}>Вернуться к заявке</Link>{taskContext.item.historical_basis ? <p>Оценка на момент решения: {Number.isFinite(taskContext.item.historical_basis.risk_probability) ? `${Math.round(taskContext.item.historical_basis.risk_probability * 100)}%` : 'Недоступна'} · Расчёт от {formatMoscowDateTime(taskContext.item.historical_basis.snapshot_ts)}</p> : <p>Ручная заявка: основание — {taskContext.item.reason}. Связанного расчёта нет.</p>}</>}</section>}
          {current.signals.status === 'rejected' && <p role="alert">Не удалось загрузить решения по объекту. <button onClick={() => setRetry(v => v + 1)}>Повторить</button></p>}
          {dispatcher && current.signals.status === 'fulfilled' && !signal && <p>Сигналов по объекту пока нет. Это не подтверждает исправность объекта.</p>}
          {signals.length > 1 && <nav className="object-signal-switcher" aria-label="Сигналы объекта">{signals.map(item => <Link className={item.card_id === signal?.card_id ? 'is-selected' : ''} key={item.card_id} to={`?signal=${encodeURIComponent(item.card_id)}`} aria-current={item.card_id === signal?.card_id ? 'page' : undefined}><span>{signalLabels[item.state]}</span><time>{formatMoscowDateTime(item.first_detected)}</time><strong>{signalRisk(item)}</strong></Link>)}</nav>}
          {signal && <section className={`content-panel object-signal-context${signal.state === 'resolved' ? ' is-resolved' : ''}`} aria-labelledby="open-signal-title">
            <div className="content-panel-heading"><div><span className="page-eyebrow">Основание для внимания диспетчера</span><h2 id="open-signal-title">{signal.state === 'resolved' ? 'Сигнал' : 'Открытый сигнал'}</h2></div><span className="state-pill state-info">{signalLabels[signal.state]}</span></div>
            <div className="object-signal-context-meta"><strong>Сигнал создан при оценке риска {signalRisk(signal)}</strong><span>Расчёт от {formatMoscowDateTime(signal.last_model_update ?? signal.first_detected)}</span></div>
            {signal.state !== 'resolved' && risk?.status === 'available' && Number.isFinite(risk.risk_probability) && signal.assessment_status === 'available' && Number.isFinite(signal.risk_probability) && risk.risk_probability < signal.risk_probability && <p className="inline-note object-signal-explanation">Текущая оценка снизилась до {Math.round(risk.risk_probability * 100)}%, но сигнал остаётся открытым до решения диспетчера.</p>}
          </section>}
          <PlannedWorkPanel objectId={id} asOf={status?.mode === 'replay' ? status.virtual_time : null} />
          {signal && <SignalDecision key={signal.card_id} signal={signal} onUpdate={updated => setData(previous => ({ ...previous, signals: { ...previous.signals, value: { ...previous.signals.value, items: previous.signals.value.items.map(item => item.card_id === updated.card_id ? updated : item) } } }))} />}
          <section className="content-panel object-detail-section object-linked-requests-section"><div className="content-panel-heading"><h2>{dispatcher ? 'Связанные заявки' : 'Мои задачи по объекту'}</h2>{current.tasks.status === 'fulfilled' && <span className="page-total">Заявок: {current.tasks.value.items.length}</span>}</div>{current.tasks.status === 'rejected' ? <p role="alert">Заявки временно недоступны.</p> : current.tasks.value.items.length === 0 ? <p className="page-state page-state-empty">Связанных заявок пока нет.</p> : relatedTasks.length === 0 ? <p>Связанная заявка открыта выше.</p> : <ul className="object-linked-list">{relatedTasks.map(task => <li key={task.id}><div><strong>{task.reason}</strong><span>{task.status} · {formatMoscowDateTime(task.updated_at)}</span></div><Link to={`${dispatcher ? '/maintenance' : '/tasks'}?request_id=${task.id}`}>Открыть заявку →</Link></li>)}</ul>}</section>
          <section className="content-panel object-detail-section object-signal-work-section"><div className="content-panel-heading"><h2>Работа по сигналам и проверкам</h2></div>{current.operational.status === 'rejected' ? <p role="alert">История работы временно недоступна.</p> : current.operational.value.items.filter(item => item.card_id || item.request_id).length === 0 ? <p className="page-state page-state-empty">Связанных сигналов и проверок пока нет.</p> : <ol className="object-activity-list">{current.operational.value.items.filter(item => item.card_id || item.request_id).map(item => <li key={item.identity}><time>{formatMoscowDateTime(item.snapshot_ts)}</time><strong>{signalLabels[item.signal_state] ?? 'Проверка'}</strong>{item.request_id && String(item.request_id) !== String(signal?.active_request?.id) && <Link to={`${dispatcher ? '/maintenance' : '/tasks'}?request_id=${item.request_id}`}>Связанная заявка →</Link>}</li>)}</ol>}</section>
          {current.operational.status === 'fulfilled' && <section className="content-panel object-detail-section object-human-history-section"><div className="content-panel-heading"><h2>История человеческой работы</h2></div>{!current.operational.value.activity?.length ? <p className="page-state page-state-empty">Зарегистрированных действий пока нет.</p> : <ol className="object-activity-list">{current.operational.value.activity.map((event, index) => <li key={`${event.request_id}-${event.timestamp}-${index}`}><time>{formatMoscowDateTime(event.timestamp)}</time><strong>{event.event === 'decision' ? decisionLabels[event.to_status] ?? 'Решение зарегистрировано' : activityLabels[event.event] ?? 'Действие зарегистрировано'}</strong>{event.request_id && String(event.request_id) !== String(signal?.active_request?.id) && <Link to={`${dispatcher ? '/maintenance' : '/tasks'}?request_id=${event.request_id}`}>Открыть заявку →</Link>}{event.card_id && <Link to={`?signal=${encodeURIComponent(event.card_id)}`}>Открыть сигнал →</Link>}{event.comment && <p>{event.comment}</p>}</li>)}</ol>}</section>}
        </>}
      </aside>
    </section>
  )
}
