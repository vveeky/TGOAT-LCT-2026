import { apiFetch } from './client'
import { parseReplayStatus } from '../replay/replayStatus'

export function getReplayStatus() {
  return apiFetch('/replay/status').then(parseReplayStatus)
}

export function startReplay() {
  return apiFetch('/replay/start', { method: 'POST' })
}

export function pauseReplay() {
  return apiFetch('/replay/pause', { method: 'POST' })
}

export function resumeReplay() {
  return apiFetch('/replay/resume', { method: 'POST' })
}

export function stepReplay() {
  return apiFetch('/replay/step', { method: 'POST' })
}

export function resetReplay() {
  return apiFetch('/replay/reset', { method: 'POST' })
}

export function setReplaySpeed(speed) {
  return apiFetch('/replay/speed', {
    method: 'PATCH',
    body: JSON.stringify({ speed }),
  })
}
