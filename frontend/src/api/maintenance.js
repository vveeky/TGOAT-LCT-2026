import { apiFetch } from './client'

export function getMaintenanceRequests(params = {}) {
  const query = new URLSearchParams(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== '')
  )
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return apiFetch(`/maintenance-requests${suffix}`)
}

export function createMaintenanceRequest(body) {
  return apiFetch('/maintenance-requests', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function getMaintenanceRequest(requestId) {
  return apiFetch(`/maintenance-requests/${requestId}`)
}

export function getEligibleTechnicians(objectId) { return apiFetch(`/maintenance-requests/eligible-technicians?object_id=${objectId}`) }
export function executeMaintenanceCommand(id, action, body) { return apiFetch(`/maintenance-requests/${id}/${action}`,{method:'POST',body:JSON.stringify(body)}) }

export function updateMaintenanceRequest(requestId, body) {
  return apiFetch(`/maintenance-requests/${requestId}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}
