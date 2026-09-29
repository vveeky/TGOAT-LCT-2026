import { apiFetch } from './client'

export const WORKFLOW_UPDATED = 'tgoat:workflow-updated'
export const signalLabels = { new: 'Новый', monitoring: 'Под наблюдением', inspection_requested: 'Проверка запрошена', resolved: 'Решён' }
export const decisionLabels = { inspection_requested: 'Запросить проверку', monitoring: 'Оставить под наблюдением', false_alarm: 'Основание не подтвердилось', confirmed: 'Подтверждено диспетчером (историческая запись)' }
export function getSignals(params = {}) {
  return apiFetch(`/signals?${new URLSearchParams(params)}`)
}
export function getSignal(cardId) { return apiFetch(`/signals/${encodeURIComponent(cardId)}`) }
export async function decideSignal(cardId, body) {
  const result = await apiFetch(`/signals/${encodeURIComponent(cardId)}/decision`, { method: 'POST', body: JSON.stringify(body) })
  window.dispatchEvent(new Event(WORKFLOW_UPDATED))
  return result
}
export function riskText(signal) {
  return signal.assessment_status === 'available' && Number.isFinite(signal.risk_probability)
    ? `Риск тревожного события в ближайшие сутки: ${Math.round(signal.risk_probability * 100)}%`
    : 'Оценка временно недоступна'
}
