const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL ?? '/api/v1'
).replace(/\/$/, '')

const TOKEN_KEY = 'tgoat_api_token'
const API_REQUEST_TIMEOUT_MS = 20_000

export class ApiError extends Error {
  constructor(message, status, detail = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export function getStoredToken() {
  return window.localStorage.getItem(TOKEN_KEY)
}

export function storeToken(token) {
  window.localStorage.setItem(TOKEN_KEY, token)
}

export function clearStoredToken() {
  window.localStorage.removeItem(TOKEN_KEY)
}

export async function apiFetch(path, options = {}) {
  const token = getStoredToken()
  const headers = new Headers(options.headers)

  if (options.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  let response
  let payload
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), API_REQUEST_TIMEOUT_MS)
  const signal = options.signal
    ? AbortSignal.any([options.signal, controller.signal])
    : controller.signal

  try {
    response = await fetch(
      `${API_BASE_URL}${path}`,
      {
        ...options,
        headers,
        signal,
      }
    )
    const contentType = response.headers.get('content-type') ?? ''
    const hasNoResponseBody = response.status === 204
      || response.status === 205
      || options.method?.toUpperCase() === 'HEAD'
    payload = hasNoResponseBody
      ? null
      : contentType.includes('application/json')
      ? await response.json()
      : await response.text()
  } catch (error) {
    if (controller.signal.aborted) {
      throw new ApiError(
        'Backend API не ответил за 20 секунд. Проверьте сервер и повторите запрос.',
        0,
        error.message
      )
    }

    if (response) {
      throw new ApiError(
        'Не удалось прочитать ответ Backend API.',
        response.status,
        error.message
      )
    }

    throw new ApiError(
      'Backend API недоступен. Проверьте, что сервер запущен на порту 8000.',
      0,
      error.message
    )
  } finally {
    window.clearTimeout(timeoutId)
  }

  if (!response.ok) {
    const detail = typeof payload === 'object' && payload !== null
      ? payload.detail
      : payload
    const message = typeof detail === 'string'
      ? detail
      : `API request failed (${response.status})`

    if (response.status === 401) {
      clearStoredToken()
      window.dispatchEvent(new Event('tgoat:unauthorized'))
    }

    throw new ApiError(message, response.status, detail)
  }

  return payload
}

export function getApiBaseUrl() {
  return API_BASE_URL
}
