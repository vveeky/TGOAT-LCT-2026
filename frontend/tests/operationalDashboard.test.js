import test from 'node:test'
import assert from 'node:assert/strict'
import { booleanPointInPolygon, point } from '@turf/turf'

import { buildOperationalDashboardModel } from '../src/api/operationalDashboard.js'
import { createSyntheticObjectLocations } from '../src/map/syntheticObjectLocations.js'


test('dashboard exposes only authoritative available ML probabilities', () => {
  const model = buildOperationalDashboardModel({
    cycle_status: 'completed',
    stale: false,
    object_count: 2,
    positive_count: 1,
    negative_count: 0,
    degraded_count: 1,
    no_prediction_count: 0,
    items: [
      { object_id: 10, status: 'available', risk_probability: 0.72, risk_flag: true,
        snapshot_ts: '2026-06-30T20:59:59Z', prediction_identity: '10|snapshot|model' },
      { object_id: 20, status: 'degraded', risk_probability: null, risk_flag: null,
        snapshot_ts: '2026-06-30T20:59:59Z' },
    ],
  }, {
    items: [
      { id: 10, name: 'Объект 10', risk_score: 0.99, risk_score_kind: 'mock' },
      { id: 20, name: 'Объект 20', risk_score: 0.81, risk_score_kind: 'mock' },
    ],
  })

  assert.equal(model.objects[0].risk_score, 0.72)
  assert.equal(model.objects[0].risk_score_kind, 'ml')
  assert.equal(model.objects[1].risk_score, null)
  assert.equal(model.objects[1].risk_score_kind, null)
  assert.equal(model.top_objects.length, 1)
  assert.equal(model.recent_predictions[1].risk_score, null)
  assert.equal(model.degraded_count, 1)
})


test('no_prediction is not converted to zero or a normal-risk label', () => {
  const model = buildOperationalDashboardModel({
    cycle_status: 'no_cycle',
    stale: false,
    object_count: 1,
    items: [{ object_id: 7, status: 'no_prediction', risk_probability: null,
      risk_flag: null, snapshot_ts: null }],
  }, { items: [{ id: 7, name: 'Объект 7', risk_score: 0.4, risk_score_kind: 'mock' }] })

  assert.equal(model.objects[0].risk_score, null)
  assert.equal(model.objects[0].risk_level, 'Нет прогноза')
  assert.equal(model.risk_distribution.low, 0)
  assert.equal(model.recent_predictions.length, 0)
})


test('historical cycle freshness and canonical snapshot timestamp reach the UI model', () => {
  const model = buildOperationalDashboardModel({
    cycle_status: 'stale',
    stale: true,
    snapshot_ts: '2026-06-30T20:59:59Z',
    object_count: 0,
    items: [],
  }, { items: [] })

  assert.equal(model.stale, true)
  assert.equal(model.cycle_status, 'stale')
  assert.equal(model.snapshot_ts, '2026-06-30T20:59:59Z')
})


test('synthetic map locations are deterministic and remain inside their labeled polygons', () => {
  const square = (code, west) => ({
    type: 'Feature',
    properties: { code },
    geometry: {
      type: 'Polygon',
      coordinates: [[
        [west, 0], [west + 1, 0], [west + 1, 1], [west, 1], [west, 0],
      ]],
    },
  })
  const features = [square('north', 0), square('south', 2)]
  const objects = [{ id: 5343 }, { id: 28 }, { id: 5122 }]
  const first = createSyntheticObjectLocations(objects, features)
  const second = createSyntheticObjectLocations(objects, features)

  assert.deepEqual(first, second)
  assert.equal(first.length, objects.length)
  for (const marker of first) {
    const feature = features.find((item) => item.properties.code === marker.featureCode)
    assert.ok(feature)
    assert.ok(booleanPointInPolygon(point([marker.position[1], marker.position[0]]), feature))
  }
})
