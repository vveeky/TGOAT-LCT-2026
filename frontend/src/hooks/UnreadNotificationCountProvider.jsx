import { useEffect, useState } from 'react'

import { getNotifications, NOTIFICATIONS_UPDATED_EVENT } from '../api/notifications'
import { useReplayRuntime } from './useReplayRuntime'
import UnreadNotificationCountContext from './UnreadNotificationCountContext'
import { WORKFLOW_UPDATED } from '../api/signals'
import { useAuth } from '../auth/useAuth'
import { hasCapability, isDispatcher } from '../auth/permissions'

export default function UnreadNotificationCountProvider({ children }) {
  const [count, setCount] = useState(null)
  const [error, setError] = useState(null)
  const { status: replayStatus } = useReplayRuntime()
  const { user } = useAuth()
  const dispatcher = isDispatcher(user)
  const canReadNotifications = hasCapability(user, 'notifications.read')

  useEffect(() => {
    if (!dispatcher && !canReadNotifications) {
      return undefined
    }
    let active = true
    const refresh = () => {
      getNotifications({ limit: 1 })
        .then((result) => {
          if (!active) return
          setCount(result.unread_count ?? 0)
          setError(null)
        })
        .catch((nextError) => { if (active) setError(nextError.message) })
    }
    refresh()
    const interval = window.setInterval(refresh, replayStatus?.mode === 'replay' ? 1500 : 60_000)
    window.addEventListener(NOTIFICATIONS_UPDATED_EVENT, refresh)
    window.addEventListener(WORKFLOW_UPDATED, refresh)
    return () => {
      active = false
      window.clearInterval(interval)
      window.removeEventListener(NOTIFICATIONS_UPDATED_EVENT, refresh)
      window.removeEventListener(WORKFLOW_UPDATED, refresh)
    }
  }, [replayStatus?.mode, dispatcher, canReadNotifications])

  return (
    <UnreadNotificationCountContext.Provider value={{
      count: dispatcher || canReadNotifications ? count : null,
      error: dispatcher || canReadNotifications ? error : null,
    }}>
      {children}
    </UnreadNotificationCountContext.Provider>
  )
}
