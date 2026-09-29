import { useCallback, useEffect, useMemo, useRef } from 'react'

import EscapeDismissContext from './EscapeDismissContext'
import { createEscapeDismissRegistry } from './escapeDismissRegistry'

function EscapeDismissProvider({ children }) {
  const registry = useRef(null)
  if (registry.current == null) registry.current = createEscapeDismissRegistry()

  const register = useCallback((handler) => registry.current.register(handler), [])
  const dismissTop = useCallback(() => registry.current.dismissTop(), [])

  useEffect(() => {
    function onKeyDown(event) {
      if (event.key !== 'Escape' || event.defaultPrevented || !dismissTop()) return
      event.preventDefault()
      event.stopPropagation()
    }

    window.addEventListener('keydown', onKeyDown, true)
    return () => window.removeEventListener('keydown', onKeyDown, true)
  }, [dismissTop])

  const value = useMemo(() => ({ register }), [register])
  return (
    <EscapeDismissContext.Provider value={value}>
      {children}
    </EscapeDismissContext.Provider>
  )
}

export default EscapeDismissProvider
