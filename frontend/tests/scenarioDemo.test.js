import assert from 'node:assert/strict'
import test from 'node:test'

import { demoScenarios } from '../src/scenarios/demoScenarios.js'

test('demo walkthroughs cover fire, flooding, and access without asserting incidents', () => {
  assert.deepEqual(demoScenarios.map(({ id }) => id), [
    'smoke-fire', 'flooding', 'access-verification',
  ])
  for (const scenario of demoScenarios) {
    assert.ok(scenario.objectId)
    assert.ok(scenario.channelId)
    assert.match(scenario.limitation, /не|отсутств|не интегр|не созда/i)
  }
})

test('organizer-confirmed КД АВ route uses channel type reference and all eight states', () => {
  const scenario = demoScenarios.find(({ id }) => id === 'access-verification')
  assert.equal(scenario.objectId, 20)
  assert.equal(scenario.channelId, 120578)
  assert.equal(scenario.states.length, 8)
  assert.match(scenario.sourceNote, /не по ID канала/)
})
