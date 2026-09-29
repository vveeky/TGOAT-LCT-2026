import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import PlannedWorkPanel from '../src/components/PlannedWorkPanel'

const getPlannedWork = vi.hoisted(() => vi.fn())
vi.mock('../src/api/objects', () => ({ getObjectPlannedWork: getPlannedWork }))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

it('does not claim there is no work when the provided source is unmapped', async () => {
  getPlannedWork.mockResolvedValue({
    source_status: 'available', unmapped_records: 613,
    active: [], upcoming: [], recent: [],
  })
  render(<PlannedWorkPanel objectId={28} />)

  expect(await screen.findByText(/Нет плановых работ, однозначно привязанных к этому объекту/)).toBeTruthy()
  expect(screen.getByText(/613 непривязанных записей/)).toBeTruthy()
  expect(screen.queryByText(/нет работ в выбранном временном диапазоне/i)).toBeNull()
})

it('renders source deadline semantics rather than saying the time is missing', async () => {
  getPlannedWork.mockResolvedValue({
    source_status: 'available', unmapped_records: 0, active: [], recent: [],
    upcoming: [{
      planned_work_id: 'ppr-deadline', work_type: 'Передача датчиков на ППР и поверку',
      date_precision: 'date_only', temporal_constraint_kind: 'deadline',
      constraint_at: '2026-01-13T09:00:00+03:00',
    }],
  })
  render(<PlannedWorkPanel objectId={28} />)

  expect(await screen.findByText(/Передать до 09:00 · 13 января 2026/)).toBeTruthy()
  expect(screen.queryByText(/время в источнике не указано/)).toBeNull()
})

it('renders month-only planned work without inventing a day or time', async () => {
  getPlannedWork.mockResolvedValue({
    source_status: 'available', unmapped_records: 0, active: [], recent: [],
    upcoming: [{
      planned_work_id: 'to-month-only', work_type: 'ТО',
      date_precision: 'month_only', planned_start: '2026-05', planned_end: '2026-05',
    }],
  })
  const { container } = render(<PlannedWorkPanel objectId={28} />)

  expect(await screen.findByText('Май 2026')).toBeTruthy()
  expect(container.textContent).not.toMatch(/1 мая|\b\d{1,2}:\d{2}\b|МСК/i)
})

it('keeps the last planned-work data visible during Replay refreshes', async () => {
  getPlannedWork.mockResolvedValueOnce({
    source_status: 'available', unmapped_records: 0, active: [], recent: [],
    upcoming: [{ planned_work_id: 'work-1', work_type: 'ТО', date_precision: 'month_only', planned_start: '2026-05' }],
  }).mockImplementationOnce(() => new Promise(() => {}))
  const view = render(<PlannedWorkPanel objectId={28} asOf="2026-05-05T12:00:00Z" />)

  expect(await screen.findByText('Май 2026')).toBeTruthy()
  view.rerender(<PlannedWorkPanel objectId={28} asOf="2026-05-05T12:00:01Z" />)

  expect(screen.getByText('Май 2026')).toBeTruthy()
  expect(screen.queryByText(/Загружаем контекст плановых работ/)).toBeNull()
})
