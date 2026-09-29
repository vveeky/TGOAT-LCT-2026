import { afterEach, expect, it } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import DashboardKpiRow from '../src/dashboard/DashboardKpiRow'

afterEach(cleanup)

it('shows real KPI values without an empty one-point graph', async () => {
  const { container } = render(<DashboardKpiRow objectCount={348} activeRequestsCount={12} />)

  expect(screen.getByText('348')).toBeTruthy()
  expect(screen.getByText('12')).toBeTruthy()
  await waitFor(() => expect(container.querySelector('.dashboard-kpi-sparkline')).toBeNull())
})