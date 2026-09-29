import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import {
  getNotifications,
  markNotificationRead,
  NOTIFICATIONS_UPDATED_EVENT,
} from '../api/notifications'
import { useReplayRuntime } from '../hooks/useReplayRuntime'
import { formatMoscowDateTime } from '../dateTime'

function NotificationsPage() {
  const [items, setItems] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [retryKey, setRetryKey] = useState(0)
  const [busyId, setBusyId] = useState(null)
  const { status: replayStatus } = useReplayRuntime()

  const load = useCallback(async () => {
    try {
      const result = await getNotifications({ limit: 100 })
      setItems(result.items ?? [])
      setLoadError(null)
    } catch (nextError) {
      setLoadError(nextError.message)
      throw nextError
    }
  }, [])

  useEffect(() => {
    let active = true
    const refresh = () => getNotifications({ limit: 100 })
      .then((result) => {
        if (!active) return
        setItems(result.items ?? [])
        setLoadError(null)
      })
      .catch((nextError) => { if (active) setLoadError(nextError.message) })
    refresh()
    const interval = window.setInterval(refresh, replayStatus?.mode === 'replay' ? 1500 : 30_000)
    return () => {
      active = false
      window.clearInterval(interval)
    }
  }, [replayStatus?.mode, retryKey])

  async function markRead(item) {
    let mutationSucceeded = false
    setBusyId(item.id)
    setActionError(null)
    try {
      await markNotificationRead(item.id, true)
      mutationSucceeded = true
      window.dispatchEvent(new Event(NOTIFICATIONS_UPDATED_EVENT))
    } catch (nextError) {
      setActionError(nextError.message)
    } finally {
      setBusyId(null)
    }
    if (mutationSucceeded) {
      try {
        await load()
      } catch {
        // The read mutation already completed; the load error has its own retry.
      }
    }
  }

  return (
    <section className="content-page notifications-page">
      <header className="content-page-heading">
        <div>
          <span className="page-eyebrow">События системы</span>
          <h1>Уведомления</h1>
          <p>Сигналы backend по прогнозам в вашей области объектов.</p>
        </div>
        {items && <span className="page-total">{items.length} в списке</span>}
      </header>
      {loadError && <p className="page-state page-state-error" role="alert">Не удалось загрузить уведомления: {loadError}{' '}<button type="button" onClick={() => setRetryKey((key) => key + 1)}>Повторить загрузку</button></p>}
      {actionError && <p className="page-state page-state-error" role="alert">Не удалось отметить уведомление прочитанным: {actionError}</p>}
      {!loadError && items === null && <p className="page-state" role="status">Загружаю уведомления из backend…</p>}
      {!loadError && items?.length === 0 && <p className="page-state page-state-empty">Новых уведомлений нет.</p>}
      <ul className="record-list notification-list">
        {(items ?? []).map((item) => (
          <li className={`record-card notification-record${item.read ? ' is-read' : ' is-unread'}`} key={item.id}>
            <div className="record-card-heading">
              <div>
                <span className="record-id">{item.source_kind === 'operational_ml' ? 'Operational ML' : 'DEMO · mock'} · {formatMoscowDateTime(item.created_at)}</span>
                <h2>{item.title}</h2>
              </div>
              <span className={`state-pill state-${item.severity ?? 'info'}`}>{item.severity ?? 'info'}</span>
            </div>
            <p>{item.message}</p>
            <div className="record-card-actions">
              {!item.read && (
                <button className="secondary-button" type="button" disabled={busyId === item.id} onClick={() => markRead(item)}>
                  {busyId === item.id ? 'Сохраняю…' : 'Отметить прочитанным'}
                </button>
              )}
              {item.object_id != null && (
                <Link className="record-card-action" to={typeof item.prediction_id === 'string'
                  ? `/predictions/${encodeURIComponent(item.prediction_id)}?object_id=${item.object_id}`
                  : `/objects/${item.object_id}`}>
                  Открыть {typeof item.prediction_id === 'string' ? 'прогноз' : 'объект'} <span aria-hidden="true">→</span>
                </Link>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}

export default NotificationsPage
