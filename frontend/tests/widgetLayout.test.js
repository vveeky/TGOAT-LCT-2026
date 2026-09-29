import test from 'node:test'
import assert from 'node:assert/strict'
import { normalizeWidgetLayout, moveWidget, widgetIds } from '../src/dashboard/widgetLayout.js'

test('widget preferences reject corrupt layouts and retain every widget exactly once', () => {
  assert.deepEqual(normalizeWidgetLayout(null), { preset: 'rows', order: widgetIds })
  assert.deepEqual(normalizeWidgetLayout({ preset: 'bad', order: ['predictions', 'predictions', 'injected'] }), {
    preset: 'rows', order: ['predictions', 'status', 'attention', 'activeRequests'],
  })
})
test('widget drag and keyboard reorder conserve layout and can persist through JSON', () => {
  const original = normalizeWidgetLayout({ preset: 'columns' })
  const moved = moveWidget(original, 'predictions', 'status')
  assert.deepEqual(moved.order, ['predictions', 'status', 'attention', 'activeRequests'])
  assert.deepEqual(normalizeWidgetLayout(JSON.parse(JSON.stringify(moved))), moved)
  assert.equal(original.order[0], 'status')
  assert.equal(moveWidget(moved, 'injected', 'status'), moved)
})
