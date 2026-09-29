import { useState } from 'react'

import {
  pauseReplay,
  resetReplay,
  resumeReplay,
  setReplaySpeed,
  stepReplay,
  startReplay,
} from '../api/replay'
import { useAuth } from '../auth/useAuth'
import { hasCapability } from '../auth/permissions'
import { useReplayRuntime } from '../hooks/useReplayRuntime'

function localTimestamp(value) {
  return value ? new Intl.DateTimeFormat('ru-RU', {
    dateStyle: 'medium', timeStyle: 'medium', timeZone: 'Europe/Moscow',
  }).format(new Date(value)) : '—'
}

export default function ReplayControls() {
  const { status, refresh, loading: statusLoading, error: statusError } = useReplayRuntime()
  const { user } = useAuth()
  const canControl = hasCapability(user, 'replay.control')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [customSpeed, setCustomSpeed] = useState('')

  const run = async (action, refreshAfter = true) => {
    setBusy(true)
    setError(null)
    try {
      await action()
      if (refreshAfter) await refresh()
    } catch (nextError) {
      setError(nextError.message)
    } finally {
      setBusy(false)
    }
  }

  if (status?.mode !== 'replay') {
    if (statusLoading) {
      return <section className="replay-controls replay-status-loading" role="status">Проверяем статус исторического воспроизведения…</section>
    }
    if (statusError) {
      return (
        <section className="replay-controls replay-status-error" aria-label="Статус исторического воспроизведения">
          <strong>Статус DEMO replay недоступен</strong>
          <span role="alert">{statusError}</span>
          <button type="button" disabled={busy} onClick={() => run(refresh, false)}>Повторить</button>
          {error && <span role="alert">{error}</span>}
        </section>
      )
    }
    return null
  }

  const stateLabel = {
    ready: 'готово к запуску', running: 'воспроизведение', paused: 'пауза',
    completed: 'завершено', failed: 'ошибка replay',
  }[status.state] ?? status.state
  const speedDescription = (speed) => {
    if (speed % 3600 === 0) return `${speed}× · ${speed / 3600} ч/с`
    if (speed % 60 === 0) return `${speed}× · ${speed / 60} мин/с`
    return `${speed}× · ${speed} виртуальных с/с`
  }

  return (
    <section className="replay-controls" aria-label="Управление историческим воспроизведением">
      <div>
        <strong>DEMO · историческое воспроизведение</strong>
        <span>{localTimestamp(status.virtual_time)} · {stateLabel}</span>
      </div>
      {statusError && <span className="replay-status-warning" role="status">Не удалось обновить состояние replay: {statusError}</span>}
      <progress max="1" value={status.progress ?? 0} aria-label="Прогресс воспроизведения" />
      {canControl ? (
        <>
          <div className="replay-controls-actions">
            <label>
              Скорость
              <select value={[60, 600, 1800, 3600].includes(status.speed) ? status.speed : 'custom'} disabled={busy} onChange={(event) => {
                if (event.target.value !== 'custom') run(() => setReplaySpeed(Number(event.target.value)))
                else setCustomSpeed(String(status.speed))
              }}>
                {[60, 600, 1800, 3600].map((speed) => <option key={speed} value={speed}>{speedDescription(speed)}</option>)}
                <option value="custom">Своя скорость…</option>
              </select>
            </label>
            {customSpeed !== '' && (
              <label>
                Множитель 1–3600×
                <input aria-label="Своя скорость replay" type="number" min="1" max="3600" step="1"
                  value={customSpeed} disabled={busy} onChange={(event) => setCustomSpeed(event.target.value)} />
              </label>
            )}
            {customSpeed !== '' && <button type="button" disabled={busy || !/^\d+$/.test(customSpeed) || Number(customSpeed) < 1 || Number(customSpeed) > 3600}
              onClick={() => run(async () => { await setReplaySpeed(Number(customSpeed)); setCustomSpeed('') })}>Применить скорость</button>}
            {status.state === 'ready' && <button type="button" disabled={busy} onClick={() => run(startReplay)}>Старт</button>}
            {status.state === 'running' && <button type="button" disabled={busy} onClick={() => run(pauseReplay)}>Пауза</button>}
            {status.state === 'paused' && <button type="button" disabled={busy} onClick={() => run(resumeReplay)}>Продолжить</button>}
            {['ready', 'paused'].includes(status.state) && status.next_inference_at && (
              <button type="button" disabled={busy} onClick={() => run(status.pending_step ? resumeReplay : stepReplay)}>
                {status.pending_step ? 'Продолжить шаг +6 ч' : 'Шаг +6 ч · расчёты каждые 5 мин'}
              </button>
            )}
            <button type="button" disabled={busy} onClick={() => run(resetReplay)}>Сбросить</button>
          </div>
          {error && <span role="alert">Не удалось управлять replay: {error}</span>}
        </>
      ) : (
        <span>Управление воспроизведением доступно диспетчерам.</span>
      )}
    </section>
  )
}
