import {
  apiFetch,
  clearStoredToken,
  getStoredToken,
  storeToken,
} from './client'

export async function login(username, password) {
  const response = await apiFetch('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })

  storeToken(response.access_token)
  return getCurrentUser()
}

export async function logout() {
  if (getStoredToken()) await apiFetch('/auth/logout', { method: 'POST' })
  clearStoredToken()
}

export function getCurrentUser() {
  return apiFetch('/auth/me')
}
