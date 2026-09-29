import { apiFetch } from './client'

export const NOTIFICATIONS_UPDATED_EVENT = 'tgoat:notifications-updated'

export function getNotifications(params = {}) {
  const query = new URLSearchParams(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== '')
  )
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return apiFetch(`/notifications${suffix}`)
}

export function markNotificationRead(notificationId, read = true) {
  return apiFetch(`/notifications/${notificationId}`, {
    method: 'PATCH',
    body: JSON.stringify({ read }),
  })
}
