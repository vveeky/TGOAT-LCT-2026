import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { decideSignal, decisionLabels, getSignal, riskText, signalLabels } from '../api/signals'
import { formatMoscowDateTime } from '../dateTime'
import AutosizeTextarea from './AutosizeTextarea'

export default function SignalDecision({ signal, onUpdate }) {
  const [decision, setDecision] = useState('inspection_requested')
  const [reason, setReason] = useState('')
  const [comment, setComment] = useState('')
  const [priority, setPriority] = useState('normal')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(null)
  const [editingDisplay, setEditingDisplay] = useState(null)
  const pending = useRef(null)
  const editingContext = useRef(null)
  const actions = [
    { key: 'inspection_requested', label: 'Запросить проверку', description: 'Создать связанную заявку для проверки объекта.' },
    { key: 'monitoring', label: 'Оставить под наблюдением', description: 'Сохранить сигнал и продолжить наблюдение без заявки.' },
    { key: 'false_alarm', label: 'Основание не подтвердилось', description: 'Завершить обработку сигнала с фиксацией решения.' },
  ]
  const beginEdit = () => {
    if (!editingContext.current) {
      editingContext.current = { expected_version: signal.work_version, prediction_identity: signal.prediction_identity }
      setEditingDisplay(signal)
    }
  }
  async function submit(event) {
    event.preventDefault()
    if (busy) return
    const context = editingContext.current ?? { expected_version: signal.work_version, prediction_identity: signal.prediction_identity }
    const content = { expected_version: context.expected_version, prediction_identity: context.prediction_identity, decision,
      reason_code: null, comment: comment || null,
      request: decision === 'inspection_requested' ? { reason: reason.trim(), priority } : null }
    const signature = JSON.stringify({ ...content, expected_version: undefined })
    // A lost HTTP response retries the same command, not a newly generated key.
    if (pending.current?.signature !== signature) pending.current = { signature, key: crypto.randomUUID(), content }
    setBusy(true); setMessage(null)
    try {
      const result = await decideSignal(signal.card_id, { ...pending.current.content, idempotency_key: pending.current.key })
      onUpdate(result.signal)
      pending.current = null
      editingContext.current = null
      setEditingDisplay(null)
      setMessage('Решение сохранено.')
    } catch (error) {
      if (error.status === 409) {
        pending.current = null
        editingContext.current = null
        setEditingDisplay(null)
        setMessage('Ситуация уже была изменена другим диспетчером. Данные обновлены.')
        try { onUpdate(await getSignal(signal.card_id)) }
        catch { setMessage('Ситуация изменилась. Не удалось обновить данные; повторите загрузку. Введённый текст сохранён.') }
      } else setMessage('Не удалось сохранить решение. Введённый текст сохранён; повторите попытку.')
    } finally { setBusy(false) }
  }
  const basis = editingDisplay ?? signal
  const percent = basis.assessment_status === 'available' && Number.isFinite(basis.risk_probability)
    ? `${Math.round(basis.risk_probability * 100)}%`
    : null
  const reasonLength = reason.trim().length
  const commentLength = comment.trim().length
  const validationMessage = decision === 'inspection_requested' && reasonLength < 8
    ? 'Причина проверки обязательна и должна содержать минимум 8 символов.'
    : decision === 'false_alarm' && commentLength < 8
      ? 'Укажите основание минимум 8 символов, чтобы завершить сигнал.'
      : null
  return <section className="content-panel action-panel" aria-labelledby="dispatcher-decision-title">
    <div className="content-panel-heading"><div><span className="page-eyebrow">Действие по открытому сигналу</span><h2 id="dispatcher-decision-title">Решение диспетчера</h2></div><span className="state-pill state-info">{signalLabels[signal.state]}</span></div>
    <div className="decision-basis-card"><div><strong>Основание сигнала</strong><span>Именно эта оценка будет связана с решением</span></div><strong className="decision-basis-value">{percent ?? riskText(basis)}</strong><time>Расчёт от {formatMoscowDateTime(basis.last_model_update)}</time></div>
    {message && <p className="page-state inline-note" role="status">{message}</p>}
    {signal.active_request && <Link className="record-card-action" to={`/maintenance?request_id=${signal.active_request.id}`}>Открыть заявку №{signal.active_request.id} →</Link>}
    {!signal.active_request && signal.last_request && <section className="decision-request-history"><Link to={`/maintenance?request_id=${signal.last_request.id}`}>История заявки №{signal.last_request.id} →</Link>
      {signal.last_request.status==='closed' && <p>Проверка завершена. Результат принят диспетчером: {signal.last_request.accepted_by_user_id}</p>}
      {signal.last_request.status==='cancelled' && <p>Заявка отменена. Требуется новое решение диспетчера.</p>}
      {signal.last_request.latest_result && <p>Результат проверки: {signal.last_request.latest_result.summary} · {signal.last_request.latest_result.technician_user_id}</p>}
    </section>}
    {!signal.active_request && signal.state !== 'resolved' && <form className="decision-form" onSubmit={submit}>
      <div className="decision-options" role="group" aria-label="Выберите решение">{actions.map(action =>
        <button className={`decision-option${decision === action.key ? ' is-selected' : ''}`} key={action.key} type="button" aria-label={action.label} aria-pressed={decision === action.key} disabled={busy} onClick={() => { beginEdit(); setDecision(action.key) }}><strong>{action.label}</strong><span>{action.description}</span></button>)}</div>
      <div className="decision-fields">
      {decision === 'inspection_requested' && <><label>Причина проверки (обязательно) <AutosizeTextarea aria-describedby="inspection-reason-hint" aria-invalid={reason.length > 0 && reasonLength < 8} aria-label="Причина проверки" minLength={8} required value={reason} onChange={e => { beginEdit(); setReason(e.target.value) }} /></label>
        <small id="inspection-reason-hint">Минимум 8 символов, без учёта пробелов по краям.</small>
        <label>Приоритет <select value={priority} onChange={e => { beginEdit(); setPriority(e.target.value) }}>
          <option value="low">Низкий</option><option value="normal">Обычный</option><option value="high">Высокий</option><option value="urgent">Срочный</option>
        </select></label></>}
      {decision === 'false_alarm' && <p className="inline-note" role="note">Сигнал будет завершён. Укажите основание решения, минимум 8 символов.</p>}
      <label><span>{decision === 'false_alarm' ? 'Основание решения (обязательно)' : 'Комментарий'}</span>{decision !== 'false_alarm' && <span className="field-optional" aria-hidden="true">Необязательно</span>}<AutosizeTextarea aria-label={decision === 'false_alarm' ? 'Основание решения' : 'Комментарий'} aria-invalid={decision === 'false_alarm' && comment.length > 0 && commentLength < 8} minLength={decision === 'false_alarm' ? 8 : undefined} required={decision === 'false_alarm'} value={comment} onChange={e => { beginEdit(); setComment(e.target.value) }} /></label>
      </div>
      {validationMessage && <p className="field-validation-message" role="alert">{validationMessage}</p>}
      <button className="decision-submit" disabled={busy || !signal.prediction_identity || Boolean(validationMessage)} type="submit">{busy ? 'Сохраняем…' : 'Подтвердить решение'}</button>
    </form>}
    {signal.last_decision && <div className="decision-saved-summary"><span>Последнее решение</span><strong>{decisionLabels[signal.last_decision.decision] ?? 'Решение зарегистрировано'}</strong>{signal.last_decision.comment && <p>{signal.last_decision.comment}</p>}</div>}
  </section>
}
