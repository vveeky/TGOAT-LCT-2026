import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')

test('replay panel exposes backend virtual time, progress and available controls', () => {
  const controls = source('../src/components/ReplayControls.jsx')
  assert.match(controls, /DEMO · историческое воспроизведение/)
  assert.match(controls, /status\.virtual_time/)
  assert.match(controls, /status\.progress/)
  assert.match(controls, /startReplay/)
  assert.match(controls, /pauseReplay/)
  assert.match(controls, /resumeReplay/)
  assert.match(controls, /resetReplay/)
  assert.match(controls, /setReplaySpeed/)
  assert.match(controls, /stepReplay/)
  for (const speed of [60, 600, 1800, 3600]) assert.match(controls, new RegExp(`\\b${speed}\\b`))
  assert.match(controls, /min="1" max="3600"/)
  assert.match(controls, /pending_step \? resumeReplay : stepReplay/)
  assert.match(controls, /Продолжить шаг \+6 ч/)
})

test('dashboard, object list, journal and notifications refresh from replay-backed APIs', () => {
  for (const page of [
    '../src/pages/DashboardPage.jsx',
    '../src/pages/ObjectsPage.jsx',
    '../src/pages/PredictionsPage.jsx',
    '../src/pages/NotificationsPage.jsx',
  ]) {
    const pageSource = source(page)
    assert.match(pageSource, /replayStatus\?\.mode === 'replay' \? 1500 : 30_000/, page)
  }

  const map = source('../src/components/MoscowOkrugsMap.jsx')
  assert.match(map, /object\.operational_status === 'available' && object\.risk_flag === true/)
  assert.match(map, /risk_probability/)
  assert.doesNotMatch(map, /Агрегированная вероятность/)
  assert.match(map, /Показать объекты/)
})
