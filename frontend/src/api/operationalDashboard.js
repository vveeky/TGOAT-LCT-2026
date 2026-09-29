const riskLevel = (item) => {
  if (item.status !== 'available') return item.status === 'degraded' ? 'Недоступен' : 'Нет прогноза'
  return item.risk_flag ? 'high' : 'low'
}

export function buildOperationalDashboardModel(operational, reference) {
  const metadata = new Map((reference?.items ?? []).map((item) => [item.id, item]))
  const items = (operational?.items ?? []).map((item) => {
    const source = metadata.get(item.object_id) ?? {}
    const available = item.status === 'available'
    return {
      ...source,
      id: item.object_id,
      name: source.name ?? `Объект ${item.object_id}`,
      channels_count: source.channels_count ?? 0,
      operational_status: item.status,
      risk_score: available ? item.risk_probability : null,
      risk_level: riskLevel(item),
      risk_prediction_id: available ? item.prediction_identity : null,
      risk_score_kind: available ? 'ml' : null,
      risk_probability: available ? item.risk_probability : null,
      risk_flag: available ? item.risk_flag : null,
      snapshot_ts: item.snapshot_ts,
      generated_at: item.generated_at,
      degraded_reason: item.degraded_reason,
      card: item.card,
      recommendation: item.recommendation,
    }
  })
  const availableItems = items.filter((item) => item.operational_status === 'available')
  const positive = availableItems.filter((item) => item.risk_flag === true)
  const negative = availableItems.filter((item) => item.risk_flag === false)
  const recentPredictions = items
    .filter((item) => item.snapshot_ts)
    .sort((left, right) => new Date(right.snapshot_ts) - new Date(left.snapshot_ts))
    .map((item) => ({
      id: item.risk_prediction_id ?? `${item.id}:${item.snapshot_ts}`,
      object_id: item.id,
      created_at: item.generated_at ?? item.snapshot_ts,
      prediction_status: item.operational_status,
      risk_level: item.risk_level,
      risk_score: item.risk_score,
      forecast_from_hours: 0,
      forecast_to_hours: 24,
    }))

  return {
    cycle_status: operational?.cycle_status ?? 'unavailable',
    stale: operational?.stale ?? true,
    snapshot_ts: operational?.snapshot_ts ?? null,
    objects_count: operational?.object_count ?? items.length,
    active_requests_count: null,
    critical_risk_count: 0,
    high_risk_count: positive.length,
    degraded_count: items.filter((item) => item.operational_status === 'degraded').length,
    no_prediction_count: items.filter((item) => item.operational_status === 'no_prediction').length,
    available_count: availableItems.length,
    risk_distribution: {
      low: negative.length,
      medium: 0,
      high: positive.length,
      critical: 0,
    },
    objects: items,
    top_objects: positive.sort((a, b) => (b.risk_score ?? 0) - (a.risk_score ?? 0)),
    recent_predictions: recentPredictions,
  }
}
