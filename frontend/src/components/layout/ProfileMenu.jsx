import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { useEscapeDismiss } from '../../hooks/useEscapeDismiss'
import { useAuth } from '../../auth/useAuth'

const roleLabels = {
  technician: 'Техник',
  district_dispatcher: 'Окружной диспетчер',
  central_dispatcher: 'Центральный диспетчер',
  system_administrator: 'Администратор ИС',
  manager: 'Руководитель',
  analyst: 'Аналитик',
}

function ProfileMenu({ variant = 'topbar' }) {
  const { user, logout } = useAuth()
  const [open, setOpen] = useState(false)
  const [logoutError, setLogoutError] = useState(null)
  const [panelPosition, setPanelPosition] = useState(null)
  const root = useRef(null)
  const trigger = useRef(null)
  const panel = useRef(null)
  const displayName = user?.full_name ?? user?.username ?? user?.user_id ?? 'Профиль'
  const loginName = user?.username ?? user?.user_id ?? displayName
  const displayRole = roleLabels[user?.role] ?? user?.role ?? 'Пользователь'
  const initials = String(displayName)
    .split(/[\s._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map(part => part[0]?.toLocaleUpperCase('ru'))
    .join('') || 'П'

  useEscapeDismiss(open, () => setOpen(false))

  useEffect(() => {
    if (!open) return undefined
    function closeOutside(event) {
      if (root.current?.contains(event.target) || panel.current?.contains(event.target)) return
      setOpen(false)
    }
    window.addEventListener('pointerdown', closeOutside, true)
    return () => window.removeEventListener('pointerdown', closeOutside, true)
  }, [open])

  useLayoutEffect(() => {
    if (!open) return undefined

    function updatePosition() {
      const triggerBounds = trigger.current?.getBoundingClientRect()
      const panelBounds = panel.current?.getBoundingClientRect()
      if (!triggerBounds || !panelBounds) return
      const margin = 12
      const width = panelBounds.width || 232
      const height = panelBounds.height || 120
      const left = Math.max(
        margin,
        Math.min(triggerBounds.right - width, window.innerWidth - width - margin),
      )
      let top = triggerBounds.bottom + 8
      if (top + height > window.innerHeight - margin) {
        top = Math.max(margin, triggerBounds.top - height - 8)
      }
      setPanelPosition({
        left: Math.round(left) + 'px',
        top: Math.round(top) + 'px',
      })
    }

    updatePosition()
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)
    return () => {
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
    }
  }, [open, user?.roles?.length, logoutError])

  const toggle = () => {
    setLogoutError(null)
    setOpen(value => !value)
  }

  const handleLogout = () => {
    setOpen(false)
    logout().catch(() => setLogoutError('Не удалось завершить сессию на сервере. Повторите попытку.'))
  }

  const button = variant === 'sidebar'
    ? (
      <button
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label={'Профиль пользователя: ' + loginName + ', ' + displayRole}
        className="sidebar-button"
        onClick={toggle}
        ref={trigger}
        type="button"
      >
        <span aria-hidden="true" className="sidebar-profile-avatar">{initials}</span>
        <span className="sidebar-profile-copy">
          <strong className="sidebar-login-name">{loginName}</strong>
          <small className="sidebar-profile-role">{displayRole}</small>
        </span>
      </button>
    )
    : (
      <button
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label={'Профиль пользователя: ' + displayName + ', ' + displayRole}
        className="topbar-profile"
        onClick={toggle}
        ref={trigger}
        title="Профиль"
        type="button"
      >
        <span aria-hidden="true" className="topbar-profile-avatar">{initials}</span>
        <span className="topbar-profile-copy">
          <strong>{displayName}</strong>
          <small>{displayRole}</small>
        </span>
        <svg aria-hidden="true" className="topbar-profile-chevron" fill="none" viewBox="0 0 24 24">
          <path d="m7 10 5 5 5-5" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
        </svg>
      </button>
    )

  return (
    <div className={'profile-menu profile-menu-' + variant} ref={root}>
      {button}
      {open && createPortal(
        <section
          aria-label="Профиль пользователя"
          className="profile-menu-panel"
          ref={panel}
          role="menu"
          style={{
            ...(panelPosition ?? {}),
            visibility: open && panelPosition ? 'visible' : 'hidden',
          }}
        >
          <div className="dashboard-profile-menu-heading">
            <strong>{displayName}</strong>
            <span>{displayRole}</span>
            {user?.roles?.length > 1 && (
              <span>Дополнительные роли: {user.roles.slice(1).map(role => roleLabels[role] ?? role).join(', ')}</span>
            )}
            {logoutError && <span role="alert">{logoutError}</span>}
          </div>
          <div aria-hidden="true" className="dashboard-profile-menu-separator" />
          <button
            className="dashboard-profile-menu-item"
            onClick={handleLogout}
            role="menuitem"
            type="button"
          >
            <svg aria-hidden="true" className="dashboard-profile-menu-icon" fill="none" viewBox="0 0 24 24">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
            </svg>
            <span>Выйти из системы</span>
          </button>
        </section>,
        document.body,
      )}
    </div>
  )
}

export default ProfileMenu
