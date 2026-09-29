import { useContext, useEffect, useRef } from 'react'

import EscapeDismissContext from './EscapeDismissContext'

export function useEscapeDismiss(isOpen, onDismiss) {
  const context = useContext(EscapeDismissContext)
  const callback = useRef(onDismiss)

  useEffect(() => { callback.current = onDismiss }, [onDismiss])
  useEffect(() => {
    if (!isOpen || !context) return undefined
    return context.register(() => callback.current())
  }, [context, isOpen])
}
