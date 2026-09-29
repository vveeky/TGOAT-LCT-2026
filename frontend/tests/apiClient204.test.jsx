import { afterEach, expect, test, vi } from 'vitest'

import { apiFetch } from '../src/api/client.js'

afterEach(() => {
  vi.unstubAllGlobals()
})

test('204 API responses with a JSON content type are treated as empty responses', async () => {
  vi.stubGlobal('window', {
    localStorage: { getItem: () => null },
    setTimeout: globalThis.setTimeout,
    clearTimeout: globalThis.clearTimeout,
  })
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, {
    status: 204,
    headers: { 'content-type': 'application/json' },
  })))

  await expect(apiFetch('/auth/logout', { method: 'POST' })).resolves.toBeNull()
})
