import test from 'node:test'
import assert from 'node:assert/strict'

import { createEscapeDismissRegistry } from '../src/hooks/escapeDismissRegistry.js'


test('Escape dismisses only the most recently opened surface', () => {
  const registry = createEscapeDismissRegistry()
  const dismissed = []
  registry.register(() => dismissed.push('map'))
  const closePanel = registry.register(() => dismissed.push('panel'))

  assert.equal(registry.dismissTop(), true)
  assert.deepEqual(dismissed, ['panel'])
  closePanel()
  assert.equal(registry.dismissTop(), true)
  assert.deepEqual(dismissed, ['panel', 'map'])
})


test('Escape with no registered dismissible surface is a no-op', () => {
  assert.equal(createEscapeDismissRegistry().dismissTop(), false)
})
