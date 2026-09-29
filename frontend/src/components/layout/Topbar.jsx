import { useState } from 'react'
import GlobalObjectSearch from './GlobalObjectSearch'
import { Link, useLocation } from 'react-router-dom'
import { Bell, BellRing, Moon, Sun } from 'lucide'
import { MorphIcon } from 'morphicons/react'
import { useUnreadNotificationCount } from '../../hooks/useUnreadNotificationCount'
import { nextAppearanceTheme } from '../../theme'
import ProfileMenu from './ProfileMenu'
import { useAuth } from '../../auth/useAuth'
import { hasCapability, isDispatcher } from '../../auth/permissions'

function Topbar({ onThemeChange, theme }) {
  const [notificationsActive, setNotificationsActive] = useState(false)
  const { count: unreadCount, error: unreadError } = useUnreadNotificationCount()
  const location = useLocation()
  const { user } = useAuth()
  const dispatcher = isDispatcher(user)
  const canSeeNotifications = dispatcher || hasCapability(user, 'notifications.read')

  return (
    <header className="topbar">
      {(hasCapability(user, 'objects.read') || hasCapability(user, 'evidence.read')) && <GlobalObjectSearch
        initialValue={new URLSearchParams(location.search).get('search') ?? ''}
        key={location.search}
      />}

      <div className="topbar-actions">
        <button
          aria-label={`Переключить тему оформления. Сейчас: ${theme === 'graphite' ? 'Графит' : 'Классик'}`}
          aria-pressed={theme === 'graphite'}
          className="topbar-simple-button theme-toggle"
          onClick={() => onThemeChange(nextAppearanceTheme(theme))}
          title={`Переключить на ${theme === 'graphite' ? 'Классик' : 'Графит'}`}
          type="button"
        >
          <MorphIcon
            aria-hidden="true"
            icon={theme === 'graphite' ? Moon : Sun}
            reducedMotion="user"
            size={21}
            spring="snappy"
            strokeWidth={1.7}
          />
        </button>

        {canSeeNotifications && <Link
          aria-label={unreadError ? 'Уведомления, счётчик недоступен' : unreadCount == null ? 'Уведомления, счётчик загружается' : `Уведомления, непрочитанных: ${unreadCount}`}
          className="topbar-simple-button notification-button"
          title={unreadError ? 'Не удалось загрузить счётчик уведомлений' : 'Уведомления'}
          onBlur={() => setNotificationsActive(false)}
          onFocus={() => setNotificationsActive(true)}
          onPointerEnter={() => setNotificationsActive(true)}
          onPointerLeave={() => setNotificationsActive(false)}
          to="/notifications"
        >
          <MorphIcon
            aria-hidden="true"
            icon={notificationsActive ? BellRing : Bell}
            reducedMotion="user"
            size={21}
            spring="snappy"
            strokeWidth={1.7}
          />
          {unreadCount > 0 && <span className="notification-badge">{unreadCount}</span>}
          {unreadError && <span className="notification-badge notification-badge-error" aria-hidden="true">!</span>}
        </Link>}

        <ProfileMenu />
      </div>
    </header>
  )
}

export default Topbar
