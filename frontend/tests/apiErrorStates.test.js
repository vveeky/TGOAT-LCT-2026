import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { formatMoscowDateTime, formatMoscowTime } from '../src/dateTime.js'

const source = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')

test('exact prediction failures are not converted to an absent record or empty reviews', () => {
  const page = source('../src/pages/PredictionPage.jsx')
  assert.doesNotMatch(page, /\.catch\(\(\)\s*=>\s*(null|\[\])\)/)
  assert.match(page, /error\?\.status === 404/)
  assert.match(page, /Прогноз не найден или недоступен в вашей области объектов/)
  assert.match(page, /Повторить/)
  assert.match(page, /Promise\.allSettled/)
  assert.match(page, /relatedErrors/)
})

test('notification unread count remains unknown when the API request fails', () => {
  const provider = source('../src/hooks/UnreadNotificationCountProvider.jsx')
  const context = source('../src/hooks/UnreadNotificationCountContext.js')
  const topbar = source('../src/components/layout/Topbar.jsx')
  assert.match(provider, /useState\(null\)/)
  assert.doesNotMatch(provider, /setCount\(0\)/)
  assert.match(provider, /setError\(nextError\.message\)/)
  assert.match(provider, /getNotifications\(\{ limit: 1 \}\)/)
  assert.match(provider, /setCount\(result\.unread_count \?\? 0\)/)
  assert.doesNotMatch(provider, /setCount\(dispatcher \? result\.counts\.new/)
  assert.match(context, /count: null, error: null/)
  assert.match(topbar, /счётчик недоступен/)
  assert.match(topbar, /to="\/notifications"/)
  assert.match(topbar, /icon=\{notificationsActive \? BellRing : Bell\}/)
  assert.doesNotMatch(topbar, /TriangleAlert/)
  const sidebar = source('../src/components/layout/Sidebar.jsx')
  assert.match(sidebar, /label="Прогнозы"/)
  assert.doesNotMatch(sidebar, /label="Уведомления"/)
})

test('replay status polling errors are visible and can be retried without implying normal mode', () => {
  const provider = source('../src/hooks/ReplayRuntimeProvider.jsx')
  const controls = source('../src/components/ReplayControls.jsx')
  assert.match(provider, /setError\(nextError\.message/)
  assert.match(provider, /loading: canReadReplay \? loading : false/)
  assert.match(provider, /error: canReadReplay \? error : null/)
  assert.match(provider, /refresh,/)
  assert.match(controls, /Проверяем статус исторического воспроизведения/)
  assert.match(controls, /Статус DEMO replay недоступен/)
  assert.match(controls, /run\(refresh, false\)/)
  assert.match(controls, /Не удалось обновить состояние replay/)
})

test('map boundary failure leaves loading state and offers a retry', () => {
  const map = source('../src/components/MoscowOkrugsMap.jsx')
  assert.match(map, /setMapError\(error\.message/)
  assert.match(map, /!data && !mapError/)
  assert.match(map, /!data && mapError/)
  assert.match(map, /setMapRetry\(\(attempt\) => attempt \+ 1\)/)
  assert.match(map, /Не удалось загрузить границы округов/)
})

test('auth API errors are distinct from login errors and allow retrying session validation', () => {
  const gate = source('../src/auth/AuthGate.jsx')
  const provider = source('../src/auth/AuthProvider.jsx')
  assert.match(gate, /error: sessionError, refresh/)
  assert.match(gate, /Не удалось проверить сохранённую сессию/)
  assert.match(gate, /Повторить проверку API/)
  assert.equal((provider.match(/expiredSession = Boolean\(token\) && error\.status === 401 && !getStoredToken\(\)/g) ?? []).length, 2)
})

test('API requests have a bounded timeout and distinguish timeout from connection refusal', () => {
  const client = source('../src/api/client.js')
  assert.match(client, /API_REQUEST_TIMEOUT_MS = 20_000/)
  assert.match(client, /controller\.abort\(\)/)
  assert.match(client, /Backend API не ответил за 20 секунд/)
  assert.match(client, /Backend API недоступен/)
  assert.match(client, /clearTimeout\(timeoutId\)/)
})

test('initial dashboard load does not present empty lists as a successful response', () => {
  const page = source('../src/pages/DashboardPage.jsx')
  const map = source('../src/components/MoscowOkrugsMap.jsx')
  assert.match(page, /objectsReady=\{Boolean\(dashboard\)\}/)
  assert.match(map, /Загрузка operational-снимка объектов/)
  assert.match(map, /objectsReady/)
  assert.match(page, /dashboard && !topObjects\.length/)
  assert.match(page, /dashboard && !latestPredictions\.length/)
  assert.match(page, /Повторить/)
})

test('read retries are explicit and notification mutation is not conflated with list loading', () => {
  for (const pagePath of [
    '../src/pages/ObjectsPage.jsx',
    '../src/pages/ObjectPage.jsx',
    '../src/pages/ChannelPage.jsx',
    '../src/pages/PredictionsPage.jsx',
    '../src/pages/PredictionPage.jsx',
    '../src/pages/MaintenancePage.jsx',
  ]) {
    assert.match(source(pagePath), /Повторить загрузку|Повторить/)
  }
  const notifications = source('../src/pages/NotificationsPage.jsx')
  assert.match(notifications, /loadError/)
  assert.match(notifications, /actionError/)
  assert.match(notifications, /Повторить загрузку/)
})

test('object and channel scope denials are shown without exposing or retrying as transient failures', () => {
  const objectPage = source('../src/pages/ObjectPage.jsx')
  const channelPage = source('../src/pages/ChannelPage.jsx')
  for (const page of [objectPage, channelPage]) {
    assert.match(page, /errorStatus === 403 \|\| errorStatus === 404/)
    assert.match(page, /недоступен в вашей области доступа/)
    assert.match(page, /errorStatus !== 403 && errorStatus !== 404/)
  }
})

test('operational timestamps are consistently rendered in Moscow time with an explicit zone label', () => {
  const instant = '2026-09-26T00:00:00Z'
  assert.match(formatMoscowDateTime(instant), /03:00 МСК$/)
  assert.equal(formatMoscowTime(instant), '03:00 МСК')
  assert.equal(formatMoscowDateTime(null), '—')
})
