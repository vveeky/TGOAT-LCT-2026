// An older in-flight read must not undo a committed human decision in the UI.
// Only within the same runtime session: replay reset intentionally starts v0.
export function mergeSignalContext(incoming, previous) {
  const prior = new Map((previous ?? []).map(signal => [signal.card_id, signal]))
  return incoming.map(signal => {
    const existing = prior.get(signal.card_id)
    return existing?.work_version > signal.work_version ? { ...signal,
      state: existing.state, work_version: existing.work_version,
      active_request: existing.active_request, last_decision: existing.last_decision,
    } : signal
  })
}
