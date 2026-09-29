import { apiFetch } from './client'

export function getChannel(channelId) {
  return apiFetch(`/channels/${channelId}`)
}

export function getChannelHistory(channelId, params = {}) {
  const query = new URLSearchParams(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== '')
  )
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return apiFetch(`/channels/${channelId}/history${suffix}`)
}

export function getChannelPredictions(channelId) {
  return apiFetch(`/channels/${channelId}/predictions`)
}
