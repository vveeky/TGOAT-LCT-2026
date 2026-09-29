import { useCallback, useEffect, useMemo, useState } from 'react'

import { getReplayStatus } from '../api/replay'
import { replayRefreshInterval } from '../replay/replayStatus'
import ReplayRuntimeContext from './ReplayRuntimeContext'
import { hasCapability } from '../auth/permissions'
import { useAuth } from '../auth/useAuth'

export default function ReplayRuntimeProvider({ children }) {
  const { user } = useAuth()
  const canReadReplay = hasCapability(user, 'replay.read')
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const refresh = useCallback(async () => {
    if (!canReadReplay) return { mode: 'normal', state: 'disabled' }
    try {
      const value = await getReplayStatus()
      setStatus(value)
      setError(null)
      setLoading(false)
      return value
    } catch (nextError) {
      setError(nextError.message)
      setLoading(false)
      throw nextError
    }
  }, [canReadReplay])

  useEffect(() => {
    if (!canReadReplay) {
      return undefined
    }
    let active = true
    let timer
    const poll = async () => {
      try {
        const value = await getReplayStatus()
        if (!active) return
        setStatus(value)
        setError(null)
        setLoading(false)
        timer = window.setTimeout(poll, replayRefreshInterval(value))
      } catch (nextError) {
        if (active) {
          setError(nextError.message || 'Не удалось получить состояние replay API.')
          setLoading(false)
          timer = window.setTimeout(poll, 10_000)
        }
      }
    }
    poll()
    return () => {
      active = false
      window.clearTimeout(timer)
    }
  }, [canReadReplay])

  const value = useMemo(() => ({
    status: canReadReplay ? status : { mode: 'normal', state: 'disabled' },
    loading: canReadReplay ? loading : false,
    error: canReadReplay ? error : null,
    refresh,
  }), [canReadReplay, error, loading, refresh, status])
  return (
    <ReplayRuntimeContext.Provider value={value}>
      {children}
    </ReplayRuntimeContext.Provider>
  )
}
