import { createSyntheticObjectLocations } from '../map/syntheticObjectLocations.js'
import { signalLabels } from '../api/signals.js'

export function monitoringPopulation(data, features, area, showAll = false) {
  const locations = createSyntheticObjectLocations(data?.objects ?? [], features)
  const inArea = new Set(locations.filter(location => !area || location.featureCode === area).map(location => location.object.id))
  // A missing map must not hide the independent operational list.
  const scopedObjects = (data?.objects ?? []).filter(object => !features?.length || inArea.has(object.id))
  const scope = new Set(scopedObjects.map(object => object.id))
  const signals = (data?.items ?? []).filter(signal => scope.has(signal.object_id) && signal.state !== 'resolved')
  const attention = new Set(signals.map(signal => signal.object_id))
  return { signals, objects: scopedObjects.filter(object => showAll || attention.has(object.id)).map(object => ({ ...object,
    requires_attention: attention.has(object.id), human_state_label: signalLabels[signals.find(s => s.object_id === object.id)?.state] })),
    counts: { attention: signals.length, inspection: signals.filter(s => s.state === 'inspection_requested').length,
      monitoring: signals.filter(s => s.state === 'monitoring').length } }
}

export function recentAttentionSignals(signals, limit = 3) {
  const timestamp = signal => {
    const value = Date.parse(signal.last_model_update ?? signal.first_detected ?? '')
    return Number.isFinite(value) ? value : 0
  }

  return [...(signals ?? [])]
    .sort((a, b) => timestamp(b) - timestamp(a))
    .slice(0, limit)
}
