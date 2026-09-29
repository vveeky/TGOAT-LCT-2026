import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { getObject } from '../api/objects'
import { getDashboard } from '../api/dashboard'
import {
  getOperationalPrediction,
  getOperationalOutcomes,
  getOperationalReviews,
  getUserHistory,
} from '../api/operational'
import { formatMoscowDateTime } from '../dateTime'
import { decisionLabels } from '../api/signals'

const statusLabel = {
  available: 'Оценка риска',
  degraded: 'Оценка временно недоступна',
  no_prediction: 'Оценка не сформирована',
}

const initialResult = {
  key: null,
  dashboard: null,
  prediction: null,
  object: null,
  reviews: null,
  outcomes: null,
  context: null,
  error: null,
  relatedErrors: [],
  relatedLoading: false,
  loading: true,
}

function PredictionPage() {
  const { id } = useParams()
  const [result, setResult] = useState(initialResult)
  const [retryKey, setRetryKey] = useState(0)
  const requestKey = `${id}:${retryKey}`

  useEffect(() => {
    let active = true
    const update = (changes) => {
      if (!active) return
      setResult((current) => {
        const base = current.key === requestKey
          ? current
          : { ...initialResult, key: requestKey }
        const resolved = typeof changes === 'function' ? changes(base) : changes
        return { ...base, ...resolved, key: requestKey }
      })
    }

    getDashboard()
      .then((dashboard) => update({ dashboard }))
      .catch((nextError) => update((current) => ({
        relatedErrors: [...current.relatedErrors, `Сводка: ${nextError.message}`],
      })))

    getOperationalPrediction(id)
      .then(async (journalItem) => {
        update({ prediction: journalItem, loading: false, relatedLoading: true })

        const [objectResult, reviewResult, outcomeResult, contextResult] = await Promise.allSettled([
          getObject(journalItem.object_id),
          getOperationalReviews(id),
          getOperationalOutcomes(id),
          getUserHistory({object_id:journalItem.object_id,prediction_identity:id,limit:1}),
        ])
        update((current) => ({
          object: objectResult.status === 'fulfilled' ? objectResult.value : null,
          reviews: reviewResult.status === 'fulfilled' ? reviewResult.value : null,
          outcomes: outcomeResult.status === 'fulfilled' ? outcomeResult.value : null,
          context:contextResult.status==='fulfilled'?contextResult.value.items[0]:null,
          relatedErrors: [
            ...current.relatedErrors,
            ...(objectResult.status === 'rejected' ? [`Объект: ${objectResult.reason.message}`] : []),
            ...(reviewResult.status === 'rejected' ? [`Проверки: ${reviewResult.reason.message}`] : []),
            ...(outcomeResult.status === 'rejected' ? [`Результаты: ${outcomeResult.reason.message}`] : []),
          ],
          relatedLoading: false,
        }))
      })
      .catch((error) => update({ error, loading: false }))

    return () => { active = false }
  }, [id, requestKey])

  const page = result.key === requestKey ? result : initialResult
  const { dashboard, prediction, object, reviews, outcomes, error, relatedErrors, relatedLoading, loading } = page
  const probability = prediction?.risk_probability
  const notFound = error?.status === 404

  return (
    <section className="content-page prediction-page">
      <Link className="back-link" to="/predictions">История</Link>
      <header className="content-page-heading"><div><h1>Оценка риска</h1></div></header>
      {error && (
        <div className="page-state page-state-error" role="alert">
          <p>{notFound ? 'Прогноз не найден или недоступен в вашей области объектов.' : `Не удалось загрузить точный прогноз: ${error.message}`}</p>
          {!notFound && <button type="button" onClick={() => setRetryKey((value) => value + 1)}>Повторить</button>}
        </div>
      )}
      {loading && <p className="page-state" role="status">Загружаем расчёт…</p>}
      {!loading && !error && dashboard?.stale && <p className="page-state page-state-stale" role="status">Показана ранее зарегистрированная оценка.</p>}
      {prediction && (
        <article className="content-panel prediction-detail-card">
          <div className="record-card-heading">
            <div><h2>{object?.name ?? 'Название объекта недоступно'}</h2></div>
            <span className={`state-pill state-${prediction.prediction_status ?? 'unknown'}`}>{statusLabel[prediction.prediction_status] ?? 'Неизвестно'}</span>
          </div>
          {prediction.prediction_status === 'available' && Number.isFinite(probability) && (
            <p className="operational-score">{Math.round(probability * 100)}% <span>Риск тревожного события в ближайшие сутки</span></p>
          )}
          <div className="snapshot-meta">
            <span>Расчёт от<strong>{formatMoscowDateTime(prediction.snapshot_ts ?? dashboard?.snapshot_ts ?? prediction.created_at)}</strong></span>
            <span>Последняя проверка<strong>{reviews === null ? relatedLoading ? 'Загрузка…' : 'Не загружена' : decisionLabels[reviews.at(-1)?.decision] ?? 'Не зарегистрирована'}</strong></span>
            <span>Результат<strong>{outcomes === null ? relatedLoading ? 'Загрузка…' : 'Не загружен' : ({confirmed:'Подтверждено',false_alarm:'Основание не подтвердилось',unknown:'Не установлен',incident:'Событие зарегистрировано',no_incident:'Событие не зарегистрировано'})[outcomes.at(-1)?.actual_outcome] ?? 'Не зарегистрирован'}</strong></span>
          </div>
          {relatedErrors.length > 0 && (
            <div className="page-state page-state-error" role="alert">
              Не все связанные данные загрузились: {relatedErrors.join(' · ')}{' '}
              <button type="button" onClick={() => setRetryKey((value) => value + 1)}>Повторить загрузку</button>
            </div>
          )}
          <details className="record-identity"><summary>Технические сведения</summary><pre>{JSON.stringify(prediction,null,2)}</pre></details>
          {page.context?.card_id&&<p><Link to={`/objects/${prediction.object_id}?signal=${encodeURIComponent(page.context.card_id)}`}>Открыть связанный сигнал</Link></p>}
          {page.context?.decision&&<p>Решение диспетчера: {decisionLabels[page.context.decision]??'Решение зарегистрировано'}</p>}
          {page.context?.request_id&&<p><Link to={`/maintenance?request_id=${page.context.request_id}`}>Открыть связанную заявку</Link></p>}
          <Link className="primary-button" to={`/objects/${prediction.object_id}`}>Открыть объект и историю <span aria-hidden="true">→</span></Link>
        </article>
      )}
    </section>
  )
}

export default PredictionPage
