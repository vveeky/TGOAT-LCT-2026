import { apiFetch } from './client'
import { buildOperationalDashboardModel } from './operationalDashboard'

export function getDashboard() {
  return Promise.all([
    apiFetch('/operational/dashboard'),
    apiFetch('/objects?limit=1000'),
  ]).then(([operational, reference]) => buildOperationalDashboardModel(operational, reference))
}
