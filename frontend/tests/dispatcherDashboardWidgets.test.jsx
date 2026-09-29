import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const getDashboard = vi.hoisted(() => vi.fn())
const getMaintenanceRequests = vi.hoisted(() => vi.fn())
let resizeObservers = []
let storage

vi.mock('../src/api/dashboard', () => ({ getDashboard }))
vi.mock('../src/api/maintenance', () => ({ getMaintenanceRequests }))
vi.mock('../src/hooks/useReplayRuntime', () => ({ useReplayRuntime: () => ({ status: { mode: 'live' } }) }))
vi.mock('../src/components/ui/risk-distribution-widget', () => ({
  default: () => <div aria-label="Распределение рисков" />,
}))
vi.mock('animejs', () => ({
  createLayout: () => ({ update: callback => callback(), revert: () => {} }),
}))

import DispatcherDashboardWidgets from '../src/dashboard/DispatcherDashboardWidgets'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

beforeEach(() => {
  const values = new Map()
  storage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: key => values.delete(key),
    clear: () => values.clear(),
  }
  Object.defineProperty(window, 'localStorage', { configurable: true, value: storage })
  vi.stubGlobal('localStorage', storage)
  resizeObservers = []
})

function renderWidgets({ observeSize = false } = {}) {
  vi.stubGlobal('matchMedia', () => ({ matches: false }))
  if (observeSize) {
    vi.stubGlobal('ResizeObserver', class ResizeObserverMock {
      constructor(callback) {
        this.callback = callback
        this.targets = []
        resizeObservers.push(this)
      }

      observe(target) { this.targets.push(target) }
      disconnect() {}
    })
  }
  getDashboard.mockResolvedValue({
    risk_distribution: { critical: 0, high: 0, medium: 0, low: 0 },
    no_prediction_count: 0,
    degraded_count: 0,
    objects_count: 0,
    objects: [],
    recent_predictions: [],
  })
  getMaintenanceRequests.mockResolvedValue({ total: 0, items: [] })
  return render(<MemoryRouter><DispatcherDashboardWidgets /></MemoryRouter>)
}

test('hover selects a widget for layout after two seconds; leaving closes it after four seconds', async () => {
  vi.useFakeTimers()
  renderWidgets()

  const grip = screen.getByRole('button', { name: 'Выбрать или переместить виджет «Требуют внимания»' })
  fireEvent.pointerEnter(grip)
  expect(grip.getAttribute('aria-expanded')).toBe('false')

  await act(async () => { await vi.advanceTimersByTimeAsync(1999) })
  expect(screen.queryByRole('dialog', { name: 'Куда переместить виджет' })).toBeNull()
  await act(async () => { await vi.advanceTimersByTimeAsync(1) })
  expect(screen.getByRole('dialog', { name: 'Куда переместить виджет' })).toBeTruthy()

  fireEvent.pointerLeave(grip)
  await act(async () => { await vi.advanceTimersByTimeAsync(3999) })
  expect(screen.getByRole('dialog', { name: 'Куда переместить виджет' })).toBeTruthy()
  await act(async () => { await vi.advanceTimersByTimeAsync(1) })
  expect(screen.queryByRole('dialog', { name: 'Куда переместить виджет' })).toBeNull()
})

test('click opens the widget chooser, toggles closed, and Escape dismisses it', () => {
  renderWidgets()
  const grip = screen.getByRole('button', { name: 'Выбрать или переместить виджет «Требуют внимания»' })

  fireEvent.click(grip)
  expect(screen.getByRole('dialog', { name: 'Выбор виджета' })).toBeTruthy()
  expect(grip.getAttribute('aria-expanded')).toBe('true')
  expect(grip.getAttribute('aria-controls')).toBe('dashboard-widget-picker-attention')

  fireEvent.click(grip)
  expect(screen.queryByRole('dialog', { name: 'Выбор виджета' })).toBeNull()

  fireEvent.click(grip)
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('dialog', { name: 'Выбор виджета' })).toBeNull()
  expect(grip.getAttribute('aria-expanded')).toBe('false')
})

test('choosing a widget swaps it with the current one and persists the selection', () => {
  renderWidgets()
  const grip = screen.getByRole('button', { name: 'Выбрать или переместить виджет «Требуют внимания»' })

  fireEvent.click(grip)
  fireEvent.click(screen.getByRole('button', { name: /Последние прогнозыНедавние прогнозы/ }))

  expect([...document.querySelectorAll('.dashboard-widget-slot')].map(slot => slot.dataset.widgetId)).toEqual([
    'status', 'predictions', 'attention',
  ])
  expect(JSON.parse(window.localStorage.getItem('tgoat.dashboard.dispatcher-widget-layout.v1'))).toMatchObject({
    order: ['predictions', 'attention', 'activeRequests'],
    presetId: 'rows',
    ordersByPreset: {
      rows: ['predictions', 'attention', 'activeRequests'],
      columns: ['predictions', 'attention', 'activeRequests'],
    },
  })
  expect(screen.queryByRole('dialog', { name: 'Куда переместить виджет' })).toBeNull()
  expect(screen.queryByRole('dialog', { name: 'Выбор виджета' })).toBeNull()
})

test('clicking another grip switches the widget chooser to that widget', () => {
  renderWidgets()
  const attentionGrip = screen.getByRole('button', { name: 'Выбрать или переместить виджет «Требуют внимания»' })
  const predictionsGrip = screen.getByRole('button', { name: 'Выбрать или переместить виджет «Последние прогнозы»' })

  fireEvent.click(attentionGrip)
  fireEvent.click(predictionsGrip)
  expect(attentionGrip.getAttribute('aria-expanded')).toBe('false')
  expect(predictionsGrip.getAttribute('aria-expanded')).toBe('true')
  expect(screen.getByRole('dialog', { name: 'Выбор виджета' })).toBeTruthy()
  fireEvent.pointerDown(document.body)
  expect(screen.queryByRole('dialog', { name: 'Выбор виджета' })).toBeNull()
})

test('hover layout choices and keyboard reordering update the actual widget positions', async () => {
  vi.useFakeTimers()
  renderWidgets()
  const grip = screen.getByRole('button', { name: 'Выбрать или переместить виджет «Требуют внимания»' })
  fireEvent.pointerEnter(grip)
  await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
  fireEvent.click(screen.getByRole('button', { name: 'Широкая сверху, ячейка 2' }))

  const grid = document.querySelector('.dashboard-adjustable-widget-layout')
  expect(grid.getAttribute('data-layout-preset')).toBe('feature-top')
  expect([...grid.querySelectorAll('.dashboard-widget-slot')].map(slot => slot.dataset.widgetId)).toEqual([
    'predictions', 'attention', 'activeRequests',
  ])
  expect(JSON.parse(window.localStorage.getItem('tgoat.dashboard.dispatcher-widget-layout.v1'))).toMatchObject({
    order: ['predictions', 'attention', 'activeRequests'],
    presetId: 'feature-top',
    ordersByPreset: {
      'feature-top': ['predictions', 'attention', 'activeRequests'],
      rows: ['attention', 'predictions', 'activeRequests'],
    },
  })

  const predictionsGrip = screen.getByRole('button', { name: 'Выбрать или переместить виджет «Последние прогнозы»' })
  fireEvent.keyDown(predictionsGrip, { key: 'ArrowDown' })
  expect([...grid.querySelectorAll('.dashboard-widget-slot')].map(slot => slot.dataset.widgetId)).toEqual([
    'attention', 'predictions', 'activeRequests',
  ])
})

test('ResizeObserver updates each widget mode from its measured cell dimensions', () => {
  renderWidgets({ observeSize: true })
  const observer = resizeObservers[0]
  expect(observer).toBeTruthy()

  act(() => observer.callback(observer.targets.map(target => {
    const dimensions = {
      status: { width: 520, height: 300 },
      attention: { width: 400, height: 240 },
      predictions: { width: 200, height: 280 },
      activeRequests: { width: 400, height: 240 },
    }[target.dataset.widgetId]
    return { target, contentRect: dimensions }
  })))

  expect(document.querySelector('[data-widget-id="status"]').getAttribute('data-layout-mode')).toBe('full')
  expect(document.querySelector('[data-widget-id="attention"]').getAttribute('data-layout-mode')).toBe('wide')
  expect(document.querySelector('[data-widget-id="predictions"]').getAttribute('data-layout-mode')).toBe('tall')
  expect(document.querySelector('[data-widget-id="activeRequests"]')).toBeNull()
})

test('dragging a widget onto another widget changes its position', () => {
  renderWidgets()
  const dragGrip = screen.getByRole('button', { name: 'Выбрать или переместить виджет «Требуют внимания»' })
  const dataTransfer = {
    effectAllowed: '',
    dropEffect: '',
    setData: vi.fn(),
    getData: vi.fn(() => 'attention'),
  }

  fireEvent.dragStart(dragGrip, { dataTransfer })
  fireEvent.dragOver(document.querySelector('[data-widget-id="predictions"]'), { dataTransfer })
  fireEvent.drop(document.querySelector('[data-widget-id="predictions"]'), { dataTransfer })

  expect([...document.querySelectorAll('.dashboard-widget-slot')].map(slot => slot.dataset.widgetId)).toEqual([
    'status', 'predictions', 'attention',
  ])
  expect(dataTransfer.setData).toHaveBeenCalledWith('text/plain', 'attention')
})

test('dragging a widget onto a layout preview applies both the cell and the preset', () => {
  renderWidgets()
  const grip = screen.getByRole('button', { name: 'Выбрать или переместить виджет «Требуют внимания»' })
  const dataTransfer = {
    effectAllowed: '',
    dropEffect: '',
    setData: vi.fn(),
    getData: vi.fn(() => 'attention'),
  }

  fireEvent.dragStart(grip, { dataTransfer })
  expect(screen.getByRole('dialog', { name: 'Куда переместить виджет' })).toBeTruthy()
  fireEvent.dragOver(screen.getByRole('button', { name: 'Широкая сверху, ячейка 2' }), { dataTransfer })
  fireEvent.drop(screen.getByRole('button', { name: 'Широкая сверху, ячейка 2' }), { dataTransfer })

  const grid = document.querySelector('.dashboard-adjustable-widget-layout')
  expect(grid.getAttribute('data-layout-preset')).toBe('feature-top')
  expect([...grid.querySelectorAll('.dashboard-widget-slot')].map(slot => slot.dataset.widgetId)).toEqual([
    'predictions', 'attention', 'activeRequests',
  ])
  expect(screen.queryByRole('dialog', { name: 'Куда переместить виджет' })).toBeNull()
})

test.each([
  ['Широкая сверху', 3],
  ['Широкая снизу', 3],
  ['Три строки', 3],
  ['Две строки', 2],
  ['Одна ячейка на всю панель', 1],
  ['Две вертикальные ячейки', 2],
])('%s renders exactly one widget in each of its %i slots', async (title, capacity) => {
  vi.useFakeTimers()
  renderWidgets()
  const grip = screen.getByRole('button', { name: 'Выбрать или переместить виджет «Требуют внимания»' })
  fireEvent.pointerEnter(grip)
  await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
  fireEvent.click(screen.getByRole('button', { name: `${title}, ячейка 1` }))

  const grid = document.querySelector('.dashboard-adjustable-widget-layout')
  const slots = [...grid.querySelectorAll(':scope > .dashboard-widget-slot')]
  expect(slots).toHaveLength(capacity)
  expect(new Set(slots.map(slot => slot.dataset.widgetId)).size).toBe(capacity)
})

test('reset restores the default widget assignments and layout preset', async () => {
  vi.useFakeTimers()
  storage.setItem('tgoat.dashboard.dispatcher-widget-layout.v1', JSON.stringify({
    order: ['predictions', 'attention', 'activeRequests'],
    presetId: 'feature-top',
  }))
  renderWidgets()

  const grip = screen.getByRole('button', { name: 'Выбрать или переместить виджет «Последние прогнозы»' })
  fireEvent.pointerEnter(grip)
  await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
  fireEvent.click(screen.getByRole('button', { name: 'Сбросить' }))

  const grid = document.querySelector('.dashboard-adjustable-widget-layout')
  expect(grid.getAttribute('data-layout-preset')).toBe('rows')
  expect([...grid.querySelectorAll('.dashboard-widget-slot')].map(slot => slot.dataset.widgetId)).toEqual([
    'attention', 'predictions',
  ])
  expect(JSON.parse(window.localStorage.getItem('tgoat.dashboard.dispatcher-widget-layout.v1'))).toMatchObject({
    order: ['attention', 'predictions', 'activeRequests'],
    presetId: 'rows',
    ordersByPreset: {
      rows: ['attention', 'predictions', 'activeRequests'],
      columns: ['predictions', 'attention', 'activeRequests'],
    },
  })
})

test('attention widget grip opens the widget chooser', () => {
  renderWidgets()
  const grip = screen.getByRole('button', { name: 'Выбрать или переместить виджет «Требуют внимания»' })

  fireEvent.click(grip)

  expect(grip.getAttribute('aria-expanded')).toBe('true')
  expect(screen.getByRole('dialog', { name: 'Выбор виджета' })).toBeTruthy()
})
