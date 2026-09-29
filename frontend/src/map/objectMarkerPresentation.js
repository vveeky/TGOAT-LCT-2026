export function getObjectMapRisk(object, positive = object?.risk_flag === true) {
  const risk = object?.risk_level
  if (risk === 'low') return 'normal'
  if (['medium', 'high', 'critical'].includes(risk)) return risk
  if (object?.operational_status === 'available') {
    return positive ? 'high' : 'normal'
  }
  return 'no-data'
}

export function hasUnresolvedSignal(object) {
  return object?.requires_attention === true
    || ['new', 'monitoring', 'inspection_requested'].includes(object?.human_state)
}

export function createObjectMarkerMarkup(object, positive = object?.risk_flag === true) {
  const risk = getObjectMapRisk(object, positive)
  const signalBadge = hasUnresolvedSignal(object)
    ? '<span class="watermelon-object-marker__signal" aria-hidden="true"></span>'
    : ''

  return `<span class="watermelon-object-marker is-${risk}" aria-hidden="true"><span class="watermelon-object-marker__dot"></span>${signalBadge}</span>`
}

export function getClusterPresentation(markers) {
  const openSignalCount = markers.filter(({ object }) => hasUnresolvedSignal(object)).length
  return {
    className: 'object-risk-cluster',
    objectCount: markers.length,
    openSignalCount,
  }
}
