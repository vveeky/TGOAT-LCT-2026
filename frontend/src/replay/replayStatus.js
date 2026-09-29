const REPLAY_STATES = new Set(['ready', 'running', 'paused', 'completed', 'failed'])

export function parseReplayStatus(payload) {
  if (!payload || !['normal', 'replay'].includes(payload.mode)) {
    throw new TypeError('Invalid runtime mode response')
  }
  if (payload.mode === 'normal') return { mode: 'normal', state: 'disabled' }
  if (!REPLAY_STATES.has(payload.state) || !Number.isFinite(payload.speed)
      || !Number.isFinite(payload.progress) || !Number.isFinite(payload.covered_object_count)) {
    throw new TypeError('Invalid replay status response')
  }
  return {
    ...payload,
    progress: Math.min(1, Math.max(0, payload.progress)),
    virtual_time: payload.virtual_time ?? null,
  }
}

export function replayRefreshInterval(status) {
  return status?.mode === 'replay' ? 1200 : 30_000
}
