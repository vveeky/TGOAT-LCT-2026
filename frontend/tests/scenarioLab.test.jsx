import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import ScenarioLabPage from '../src/pages/ScenarioLabPage'

const api = vi.hoisted(() => ({ fetch: vi.fn() }))
vi.mock('../src/api/client', () => ({ apiFetch: api.fetch }))

function response(overrides = {}) {
  return {
    object_id: 28,
    scenario_type: 'fire',
    title: 'Пожарные признаки',
    as_of: '2026-09-28T09:00:00Z',
    history_source: 'historical_replay',
    classification: 'observed_sensor_alarm_requires_verification',
    generic_proxy_risk: null,
    observability: { status: 'currently_observed', fresh_channel_count: 1, relevant_channel_count: 1 },
    evidence: [], missing_evidence: [],
    planned_work: { active: [], recent: [], upcoming: [], unmapped_records: 0 },
    recommendations: [], decision_route: '/objects/28',
    equipment_terminology: { abbreviations: { ФВ: 'фидер вентиляции' }, designation_rules: [] },
    ...overrides,
  }
}

afterEach(() => { cleanup(); vi.clearAllMocks() })

it('exposes all customer confirmed incident and observability scenario groups', async () => {
  api.fetch.mockResolvedValue(response())
  render(<MemoryRouter><ScenarioLabPage /></MemoryRouter>)
  for (const label of ['Пожарные признаки', 'Подтопление', 'Газ', 'Охрана и доступ',
    'Аномальная температура', 'Потеря связи', 'Электроснабжение']) {
    expect(screen.getByRole('button', { name: label })).toBeTruthy()
  }
  expect(await screen.findByText(/Тревожное сообщение означает срабатывание источника/)).toBeTruthy()
})

it('includes gas alarms in the operational verification flow', async () => {
  api.fetch.mockImplementation(async (path) => path.includes('/gas/')
    ? response({ scenario_type: 'gas', title: 'Признаки загазованности',
      evidence: [{ channel_id: 4, sensor_type: 'Газовый датчик', sensor_name: 'Газ ПК10',
        state: 'alarm_observed', latest_at: '2026-09-28T08:59:00Z', sampled_observations: 1,
        sampled_alarms: 1 }] })
    : response())
  render(<MemoryRouter><ScenarioLabPage /></MemoryRouter>)
  await userEvent.click(await screen.findByRole('button', { name: 'Газ' }))
  expect(await screen.findByText('Признаки загазованности · объект 28')).toBeTruthy()
  expect(screen.getByText('Газовый датчик · канал 4')).toBeTruthy()
  expect(screen.getByText(/требуется верификация/)).toBeTruthy()
})

it('shows intrusion events in time order and states when the sequence is incomplete', async () => {
  api.fetch.mockImplementation(async (path) => path.includes('/intrusion/')
    ? response({ scenario_type: 'intrusion', title: 'Признаки доступа', history_source: 'annual_event_history_latest_only',
      intrusion_route: { points: [
        { channel_id: 2, sensor_name: 'Дверь ПК12', timestamp: '2026-09-28T08:01:00Z' },
        { channel_id: 3, sensor_name: 'Люк ПК15', timestamp: '2026-09-28T08:03:00Z' },
      ], route_geometry: null, sequence_completeness: 'incomplete_latest_state_per_channel_only',
      limitation: 'Топология коллектора и маршрут между точками не подтверждены.' } })
    : response())
  render(<MemoryRouter><ScenarioLabPage /></MemoryRouter>)
  await userEvent.click(await screen.findByRole('button', { name: 'Охрана и доступ' }))
  expect(await screen.findByRole('heading', { name: 'Последовательность сработок охранных датчиков' })).toBeTruthy()
  const first = await screen.findByText(/Дверь ПК12/)
  const second = screen.getByText(/Люк ПК15/)
  expect(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(screen.getByText(/последние состояния каналов/)).toBeTruthy()
  expect(screen.queryByText(/incomplete_latest_state_per_channel_only/)).toBeNull()
})

it('shows customer confirmed equipment glossary in Russian UI', async () => {
  api.fetch.mockResolvedValue(response())
  render(<MemoryRouter><ScenarioLabPage /></MemoryRouter>)
  await screen.findByText('Пожарные признаки · объект 28')
  await userEvent.click(screen.getByText('Справочник оборудования и ограничений'))
  await waitFor(() => expect(screen.getByText('фидер вентиляции')).toBeTruthy())
})
