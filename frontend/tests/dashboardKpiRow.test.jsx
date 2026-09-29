import { afterEach, beforeEach, expect, test } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'

import DashboardKpiRow from '../src/dashboard/DashboardKpiRow'

afterEach(cleanup)

beforeEach(() => {
  window.sessionStorage.setItem('tgoat.dashboard.kpi-history.v1', JSON.stringify({
    objects: [{ at: 1, value: 16 }, { at: 2, value: 18 }],
    activeRequests: [{ at: 1, value: 3 }, { at: 2, value: 4 }],
  }))
})

test('shows two trend KPIs without an extra attention card', () => {
  render(<DashboardKpiRow activeRequestsCount={4} objectCount={18} />)

  expect(document.querySelectorAll('.dashboard-kpi-card')).toHaveLength(2)
  expect(screen.getByRole('heading', { name: 'Всего объектов' })).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'Активные заявки' })).toBeTruthy()
  expect(screen.queryByRole('heading', { name: 'Требуют внимания' })).toBeNull()
  expect(document.querySelectorAll('.dashboard-kpi-sparkline')).toHaveLength(2)
})
