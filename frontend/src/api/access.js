import { apiFetch } from './client'

export function getAccessPrincipals() {
  return apiFetch('/access/principals')
}

export function updateAccessPrincipal(userId, assignment) {
  return apiFetch(`/access/principals/${encodeURIComponent(userId)}`, {
    method: 'PUT',
    body: JSON.stringify(assignment),
  })
}
