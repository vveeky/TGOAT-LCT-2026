import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from 'react'

import {
  getCurrentUser,
  login as requestLogin,
  logout as clearSession,
} from '../api/auth'
import { getStoredToken } from '../api/client'
import AuthContext from './AuthContext'

function AuthProvider({ children }) {
  const [state, setState] = useState({
    loading: true,
    user: null,
    error: null,
  })

  const refresh = useCallback(async () => {
    const token = getStoredToken()
    const username = import.meta.env.VITE_API_USERNAME
    const password = import.meta.env.VITE_API_PASSWORD

    if (!token && (!username || !password)) {
      setState({ loading: false, user: null, error: null })
      return
    }

    try {
      const user = token
        ? await getCurrentUser()
        : await requestLogin(username, password)

      setState({ loading: false, user, error: null })
    } catch (error) {
      const expiredSession = Boolean(token) && error.status === 401 && !getStoredToken()
      setState({ loading: false, user: null, error: expiredSession ? null : error })
    }
  }, [])

  useEffect(() => {
    let active = true

    async function initializeSession() {
      const token = getStoredToken()
      const username = import.meta.env.VITE_API_USERNAME
      const password = import.meta.env.VITE_API_PASSWORD

      if (!token && (!username || !password)) {
        if (active) setState({ loading: false, user: null, error: null })
        return
      }

      try {
        const user = token
          ? await getCurrentUser()
          : await requestLogin(username, password)

        if (active) setState({ loading: false, user, error: null })
      } catch (error) {
        const expiredSession = Boolean(token) && error.status === 401 && !getStoredToken()
        if (active) setState({ loading: false, user: null, error: expiredSession ? null : error })
      }
    }

    initializeSession()

    const handleUnauthorized = () => {
      setState({ loading: false, user: null, error: null })
    }

    window.addEventListener('tgoat:unauthorized', handleUnauthorized)

    return () => {
      active = false
      window.removeEventListener('tgoat:unauthorized', handleUnauthorized)
    }
  }, [])

  const login = useCallback(async (username, password) => {
    const user = await requestLogin(username, password)
    setState({ loading: false, user, error: null })
    return user
  }, [])

  const logout = useCallback(async () => {
    await clearSession()
    setState({ loading: false, user: null, error: null })
  }, [])

  const value = useMemo(() => ({
    ...state,
    login,
    logout,
    refresh,
  }), [login, logout, refresh, state])

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  )
}

export default AuthProvider
