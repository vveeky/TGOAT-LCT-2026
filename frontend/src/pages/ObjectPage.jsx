import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import {
  createOperationalOutcome,
  createOperationalReview,
  getOperationalForecastCards,
  getOperationalObject,
  getOperationalOutcomes,
  getOperationalRecommendations,
  getOperationalReviews,
  getOperationalRiskHistory,
} from '../api/operational'
import { getObject, getObjectChannels } from '../api/objects'
import { canRecordOperationalOutcome, canReviewOperationalPrediction } from '../auth/permissions'
import { useAuth } from '../auth/useAuth'
import { useReplayRuntime } from '../hooks/useReplayRuntime'
import { formatMoscowDateTime } from '../dateTime'
import DispatcherObjectPage from './DispatcherObjectPage'
import PlannedWorkPanel from '../components/PlannedWorkPanel'
import AutosizeTextarea from '../components/AutosizeTextarea'

export function ObjectPage() {
  const { id } = useParams()
  const [object, setObject] = useState(null)
  const [loadedId, setLoadedId] = useState(null)
  const [channelState, setChannelState] = useState({ id: null, items: [], error: null })
  const [state, setState] = useState(null)
  const [history, setHistory] = useState([])
  const [cards, setCards] = useState([])
  const [recommendations, setRecommendations] = useState([])
  const [reviews, setReviews] = useState([])
  const [outcomes, setOutcomes] = useState([])
  const [decision, setDecision] = useState('confirmed')
  const [reason, setReason] = useState('')
  const [comment, setComment] = useState('')
  const [outcome, setOutcome] = useState('')
  const [errorState, setErrorState] = useState({ id: null, message: null, status: null })
  const [actionError, setActionError] = useState(null)
  const [retryKey, setRetryKey] = useState(0)
  const [busy, setBusy] = useState(false)
  const { user } = useAuth()
  const { status: replayStatus } = useReplayRuntime()
  const canReview = canReviewOperationalPrediction(user)
  const canRecordOutcome = canRecordOperationalOutcome(user)

  const load = useCallback(async () => {
    const [objectData, current] = await Promise.all([
      getObject(id),
      getOperationalObject(id),
    ])
    const [historyData, cardData, recommendationData, reviewData, outcomeData] = await Promise.all([
      getOperationalRiskHistory(id),
      getOperationalForecastCards(id),
      getOperationalRecommendations(id),
      current.prediction_identity ? getOperationalReviews(current.prediction_identity) : Promise.resolve([]),
      current.prediction_identity ? getOperationalOutcomes(current.prediction_identity) : Promise.resolve([]),
    ])
    return {
      object: objectData,
      state: current,
      history: historyData,
      cards: cardData,
      recommendations: recommendationData,
      reviews: reviewData,
      outcomes: outcomeData,
    }
  }, [id])

  useEffect(() => {
    let active = true
    getObjectChannels(id)
      .then((value) => {
        if (active) setChannelState({ id, items: value.items ?? [], error: null })
      })
      .catch((nextError) => {
        if (active) setChannelState({ id, items: [], error: nextError.message })
      })
    return () => { active = false }
  }, [id])

  useEffect(() => {
    let active = true
    const refresh = () => load()
      .then((data) => {
        if (!active) return
        setObject(data.object)
        setState(data.state)
        setHistory(data.history)
        setCards(data.cards)
        setRecommendations(data.recommendations)
        setReviews(data.reviews)
        setOutcomes(data.outcomes)
        setLoadedId(id)
        setErrorState({ id, message: null, status: null })
      })
      .catch((nextError) => { if (active) setErrorState({ id, message: nextError.message, status: nextError.status }) })
    refresh()
    const interval = window.setInterval(refresh, replayStatus?.mode === 'replay' ? 1500 : 30_000)
    return () => {
      active = false
      window.clearInterval(interval)
    }
  }, [id, load, replayStatus?.mode, retryKey])

  const currentRecordLoaded = loadedId === id
  const pageObject = currentRecordLoaded ? object : null
  const pageState = currentRecordLoaded ? state : null
  const pageChannels = channelState.id === id ? channelState.items : []
  const pageChannelsError = channelState.id === id ? channelState.error : null
  const pageChannelsLoading = channelState.id !== id
  const error = errorState.id === id ? errorState.message : null
  const errorStatus = errorState.id === id ? errorState.status : null

  async function submitAction(action) {
    if (!pageState?.prediction_identity) return
    let actionSaved = false
    setBusy(true)
    setActionError(null)
    try {
      const idempotency_key = globalThis.crypto?.randomUUID?.()
        ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`
      if (action === 'review') {
        await createOperationalReview(pageState.prediction_identity, {
          decision,
          reason_code: reason || null,
          comment: comment || null,
          idempotency_key,
        })
      } else {
        await createOperationalOutcome(pageState.prediction_identity, {
          actual_outcome: outcome,
          reason_code: reason || null,
          comment: comment || null,
          idempotency_key,
        })
        setOutcome('')
      }
      actionSaved = true
      const data = await load()
      setObject(data.object)
      setState(data.state)
      setHistory(data.history)
      setCards(data.cards)
      setRecommendations(data.recommendations)
      setReviews(data.reviews)
      setOutcomes(data.outcomes)
      setLoadedId(id)
      setErrorState({ id, message: null })
    } catch (nextError) {
      setActionError(actionSaved
        ? `Запись сохранена, но не удалось обновить данные: ${nextError.message}`
        : `Не удалось сохранить действие: ${nextError.message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="content-page object-page">
      <Link className="back-link" to="/objects">← К списку объектов</Link>
      <header className="content-page-heading">
        <div>
          <span className="page-eyebrow">Объект · {id}</span>
          <h1>{pageObject?.name ?? `Объект ${id}`}</h1>
        </div>
        <Link className="primary-button" to={`/maintenance?object_id=${id}`}>Создать заявку</Link>
      </header>
      {error && (
        <p className="page-state page-state-error" role="alert">
          {errorStatus === 403 || errorStatus === 404
            ? 'Объект не найден или недоступен в вашей области доступа.'
            : `Не удалось загрузить данные объекта: ${error}`}
          {errorStatus !== 403 && errorStatus !== 404 && <>{' '}<button type="button" onClick={() => setRetryKey((key) => key + 1)}>Повторить загрузку</button></>}
        </p>
      )}
      {!pageState && !error && <p className="page-state" role="status">Загружаю состояние из backend…</p>}
      {pageState && (
        <>
          <PlannedWorkPanel objectId={id} />
          <section className="content-panel object-risk-panel">
          <div className="content-panel-heading">
            <h2>Оперативный прогноз</h2>
            <span className={`state-pill state-${pageState.status}`}>
              {pageState.status === 'available' ? 'ML-прогноз доступен' : pageState.status === 'degraded' ? 'ML временно недоступен' : 'Прогноза нет'}
            </span>
          </div>
          {pageState.status === 'available' && (
            <p className="operational-score">{Math.round(pageState.risk_probability * 100)}% <span>ML · порог {Math.round(pageState.threshold * 100)}%</span></p>
          )}
          {pageState.degraded_reason && <p className="page-state page-state-stale">Причина недоступности: {pageState.degraded_reason}</p>}
          <div className="snapshot-meta">
            <span>Снимок<strong>{formatMoscowDateTime(pageState.snapshot_ts)}</strong></span>
            <span>Модель<strong>{pageState.model_version ?? '—'}</strong></span>
            <span>Схема<strong>{pageState.feature_schema_version ?? '—'}</strong></span>
          </div>
          <details className="record-identity"><summary>Точная identity прогноза</summary><code>{pageState.prediction_identity ?? '—'}</code></details>
          </section>

          <section className="content-panel">
          <div className="content-panel-heading"><h2>Каналы объекта</h2>{pageObject && <span className="page-total">{pageObject.channels_count ?? pageChannels.length} каналов</span>}</div>
          {pageChannelsLoading && <p className="page-state" role="status">Загружаю каналы объекта…</p>}
          {pageChannelsError && <p className="page-state page-state-error" role="alert">Не удалось загрузить каналы: {pageChannelsError}{' '}<button type="button" onClick={() => setRetryKey((key) => key + 1)}>Повторить загрузку</button></p>}
          {!pageChannelsLoading && !pageChannelsError && pageChannels.length === 0 && <p className="page-state page-state-empty">Каналы не найдены.</p>}
          <ul className="record-list channel-list">
            {pageChannels.map((channel) => (
              <li className="record-card channel-record" key={channel.id}>
                <div><span className="record-id">Канал · {channel.id}</span><h3><Link to={`/channels/${channel.id}`}>{channel.name || `Канал ${channel.id}`}</Link></h3></div>
                <span>{channel.sensor_type ?? 'Тип не указан'}</span>
                <span>{channel.system_type ?? 'Система не указана'}</span>
                <Link className="record-card-action" to={`/channels/${channel.id}`}>История канала <span aria-hidden="true">→</span></Link>
              </li>
            ))}
          </ul>
          </section>
          {pageState.prediction_identity && pageState.status === 'available' && canReview && (
            <section className="content-panel action-panel">
              <>
              <div className="content-panel-heading"><h2>Проверка прогноза</h2><span className="state-pill state-info">Запись оператора</span></div>
              {actionError && <p className="page-state page-state-error" role="alert">{actionError}</p>}
              <label>
                Решение{' '}
                <select value={decision} onChange={(event) => setDecision(event.target.value)}>
                  <option value="confirmed">Подтвердить</option>
                </select>
              </label>
              <p><label>Код причины <input value={reason} onChange={(event) => setReason(event.target.value)} /></label></p>
              <p><label>Комментарий <AutosizeTextarea value={comment} onChange={(event) => setComment(event.target.value)} /></label></p>
              <button type="button" disabled={busy} onClick={() => submitAction('review')}>Сохранить решение</button>
              {canRecordOutcome ? (
                <>
                  <h3>Зафиксировать результат проверки</h3>
                  <p><label>Фактический результат <input value={outcome} onChange={(event) => setOutcome(event.target.value)} /></label></p>
                  <button type="button" disabled={busy || !outcome.trim()} onClick={() => submitAction('outcome')}>Сохранить результат</button>
                </>
              ) : <p>Фактический результат проверки фиксирует диспетчер.</p>}
              </>
            </section>
          )}
          {pageState.prediction_identity && pageState.status === 'available' && !canReview && (
            <p className="page-state page-state-stale">Ваша роль не может оставлять review по прогнозу.</p>
          )}
          <section className="content-panel">
          <h2>Решения и результаты</h2>
          {!reviews.length && !outcomes.length && <p className="page-state page-state-empty">Записей пока нет.</p>}
          <ul className="record-list compact-record-list">
            {reviews.map((item) => <li className="record-card" key={`review-${item.id}`}><strong>{item.decision}</strong><span>{item.actor_role} · {formatMoscowDateTime(item.created_at)}</span>{item.comment && <p>{item.comment}</p>}</li>)}
            {outcomes.map((item) => <li className="record-card" key={`outcome-${item.id}`}><strong>Результат: {item.actual_outcome}</strong><span>{item.actor_role} · {formatMoscowDateTime(item.created_at)}</span>{item.comment && <p>{item.comment}</p>}</li>)}
          </ul>
          </section>
          <div className="object-support-grid">
          <section className="content-panel">
          <h2>Карточки прогноза</h2>
          {cards.length === 0 && <p className="page-state page-state-empty">Карточек пока нет.</p>}
          <ul className="record-list compact-record-list">{cards.map((card) => <li className="record-card" key={card.card_id}><strong>{card.status} · {Math.round(card.max_probability * 100)}%</strong><span>{formatMoscowDateTime(card.first_positive_snapshot_ts)}</span></li>)}</ul>
          </section>
          <section className="content-panel">
          <h2>Рекомендации</h2>
          {recommendations.length === 0 && <p className="page-state page-state-empty">Рекомендаций пока нет.</p>}
          <ul className="record-list compact-record-list">{recommendations.slice(0, 10).map((item) => <li className="record-card" key={item.recommendation_id}><strong>{item.status}</strong><span>{item.action ?? item.code ?? '—'}</span></li>)}</ul>
          </section>
          </div>
          <section className="content-panel">
          <h2>История прогнозов</h2>
          {history.length === 0 && <p className="page-state page-state-empty">История прогнозов пуста.</p>}
          <ul className="record-list compact-record-list">{history.slice(0, 20).map((item) => <li className="record-card" key={item.identity}><strong>{formatMoscowDateTime(item.snapshot_ts)}</strong><span>{item.prediction_status}{item.risk_probability == null ? '' : ` · ML ${Math.round(item.risk_probability * 100)}%`}</span></li>)}</ul>
          </section>
        </>
      )}
    </section>
  )
}

export default function RoleObjectPage() {
  return <DispatcherObjectPage />
}
