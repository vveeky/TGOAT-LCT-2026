import { apiFetch } from './client'

export function getPredictions(params = {}) {
  const query = new URLSearchParams(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== '')
  )
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return apiFetch(`/predictions${suffix}`)
}

export function getPrediction(predictionId) {
  return apiFetch(`/predictions/${predictionId}`)
}

export function reviewPrediction(predictionId, body) {
  return apiFetch(`/predictions/${predictionId}/review`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export function closePrediction(predictionId) {
  return apiFetch(`/predictions/${predictionId}/close`, {
    method: 'POST',
  })
}
