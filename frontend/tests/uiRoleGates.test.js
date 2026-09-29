import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { canRecordOperationalOutcome, canReviewOperationalPrediction } from '../src/auth/permissions.js'

const source = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')

test('dispatcher-only replay controls are hidden from other roles', () => {
  const controls = source('../src/components/ReplayControls.jsx')
  assert.match(controls, /hasCapability\(user, 'replay\.control'\)/)
  assert.match(controls, /Управление воспроизведением доступно диспетчерам/)
})

test('technicians can review in-scope predictions but only dispatchers can record outcomes', () => {
  assert.equal(canReviewOperationalPrediction('technician'), true)
  assert.equal(canRecordOperationalOutcome('technician'), false)
  for (const role of ['district_dispatcher', 'central_dispatcher']) {
    assert.equal(canReviewOperationalPrediction(role), true)
    assert.equal(canRecordOperationalOutcome(role), true)
  }
  assert.equal(canReviewOperationalPrediction('unknown'), false)
  assert.equal(canRecordOperationalOutcome('unknown'), false)
  const stacked = { role: 'central_dispatcher', roles: ['central_dispatcher', 'technician'] }
  assert.equal(canReviewOperationalPrediction(stacked), true)
  assert.equal(canRecordOperationalOutcome(stacked), true)

  const objectPage = source('../src/pages/ObjectPage.jsx')
  const maintenancePage = source('../src/pages/MaintenancePage.jsx')
  assert.match(objectPage, /canReviewOperationalPrediction\(user\)/)
  assert.match(objectPage, /canRecordOperationalOutcome\(user\)/)
  assert.match(objectPage, /pageState\.status === 'available' && canReview/)
  assert.match(objectPage, /Фактический результат проверки фиксирует диспетчер/)
  assert.match(maintenancePage, /hasCapability\(user, 'requests\.dispatch'\)/)
})
