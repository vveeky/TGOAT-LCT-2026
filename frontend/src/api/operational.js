import { apiFetch } from './client'

export function getUserHistory(params={}){return apiFetch(`/operational/history?${new URLSearchParams(Object.entries(params).filter(([,value])=>value!==''&&value!=null))}`)}

export function getOperationalDashboard() {
  return apiFetch('/operational/dashboard')
}

export function getOperationalPredictionJournal({ limit = 100, offset = 0 } = {}) {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  return apiFetch(`/operational/predictions?${query}`)
}

export function getOperationalPrediction(identity) {
  return apiFetch(`/operational/predictions/${encodeURIComponent(identity)}`)
}

export function getOperationalObject(objectId) {
  return apiFetch(`/operational/objects/${objectId}`)
}

export function getOperationalRiskHistory(objectId, limit = 100) {
  return apiFetch(`/objects/${objectId}/risk/history?limit=${limit}`)
}

export function getOperationalForecastCards(objectId, status) {
  const query = status ? `?status=${encodeURIComponent(status)}` : ''
  return apiFetch(`/objects/${objectId}/risk/cards${query}`)
}

export function getOperationalRecommendations(objectId) {
  return apiFetch(`/objects/${objectId}/risk/recommendations`)
}

export function getOperationalReviews(identity) {
  return apiFetch(`/operational/predictions/${encodeURIComponent(identity)}/reviews`)
}

export function createOperationalReview(identity, body) {
  return apiFetch(`/operational/predictions/${encodeURIComponent(identity)}/review`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function getOperationalOutcomes(identity) {
  return apiFetch(`/operational/predictions/${encodeURIComponent(identity)}/outcomes`)
}

export function createOperationalOutcome(identity, body) {
  return apiFetch(`/operational/predictions/${encodeURIComponent(identity)}/outcomes`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function getOperationalRisk(objectId, snapshotTs) {
  const query = snapshotTs ? `?snapshot_ts=${encodeURIComponent(snapshotTs)}` : ''
  return apiFetch(`/objects/${objectId}/risk${query}`)
}

export function runOperationalRisk(objectId, snapshotTs) {
  return apiFetch(`/objects/${objectId}/risk/operational?snapshot_ts=${encodeURIComponent(snapshotTs)}`, {
    method: 'POST',
  })
}
