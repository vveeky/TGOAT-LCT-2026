import { useCallback, useEffect, useId, useRef, useState } from 'react'

import { useEscapeDismiss } from '../hooks/useEscapeDismiss'

export default function MapControlsMenu({ children, label = 'Управление' }) {
  const [open, setOpen] = useState(false)
  const menuRef = useRef(null)
  const contentId = useId()
  const close = useCallback(() => setOpen(false), [])

  useEscapeDismiss(open, close)

  useEffect(() => {
    if (!open) return undefined

    const dismissOnOutsideClick = (event) => {
      if (!menuRef.current?.contains(event.target)) close()
    }

    document.addEventListener('pointerdown', dismissOnOutsideClick, true)
    return () => document.removeEventListener('pointerdown', dismissOnOutsideClick, true)
  }, [close, open])

  return (
    <div className={`map-controls-menu${open ? ' is-open' : ''}`} ref={menuRef}>
      <button
        aria-label={label}
        aria-controls={contentId}
        aria-expanded={open}
        className="map-controls-menu-trigger"
        onClick={() => setOpen((current) => !current)}
        title={label}
        type="button"
      >
        <svg aria-hidden="true" fill="none" height="16" viewBox="0 0 24 24" width="16">
          <path d="M4 7h9M17 7h3M4 17h3M11 17h9" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" />
          <circle cx="15" cy="7" r="2" stroke="currentColor" strokeWidth="1.8" />
          <circle cx="9" cy="17" r="2" stroke="currentColor" strokeWidth="1.8" />
        </svg>
        <span className="sr-only">{label}</span>
      </button>
      {open && (
        <div aria-label={label} className="map-controls-menu-popover" id={contentId} role="group">
          {children}
        </div>
      )}
    </div>
  )
}
