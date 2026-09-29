import { useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import {
  Bell,
  BellRing,
  Building,
  Building2,
  ChartNoAxesColumnIncreasing,
  ChartNoAxesCombined,
  ClipboardCheck,
  ClipboardList,
  GitBranch,
  LayoutGrid,
  PanelsTopLeft,
  Shield,
  ShieldCheck,
  TriangleAlert,
  Workflow,
  ChevronDown,
  ChevronUp,
} from 'lucide'
import { MorphIcon } from 'morphicons/react'
import { hasCapability, isDispatcher } from '../../auth/permissions'
import { useAuth } from '../../auth/useAuth'
import { useUnreadNotificationCount } from '../../hooks/useUnreadNotificationCount'
import moscollectorLogo from '../../assets/moscollector-logo.png'
import ProfileMenu from './ProfileMenu'

const SIDEBAR_ICON_STATES = {
  dashboard: [LayoutGrid, PanelsTopLeft],
  objects: [Building2, Building],
  predictions: [ChartNoAxesCombined, ChartNoAxesColumnIncreasing],
  maintenance: [ClipboardList, ClipboardCheck],
  scenarios: [Workflow, GitBranch],
  signals: [TriangleAlert, TriangleAlert],
  access: [ShieldCheck, Shield],
}

function SidebarLink({ to, label, icon, collapsed }) {
  const [interacting, setInteracting] = useState(false)
  const [defaultIcon, hoverIcon] = SIDEBAR_ICON_STATES[icon]

  return (
    <NavLink
      aria-label={label}
      className={({ isActive }) => isActive ? 'nav-link active' : 'nav-link'}
      onBlur={() => setInteracting(false)}
      onFocus={() => setInteracting(true)}
      onPointerEnter={() => setInteracting(true)}
      onPointerLeave={() => setInteracting(false)}
      title={collapsed ? label : undefined}
      to={to}
    >
      <MorphIcon
        aria-hidden="true"
        className="nav-icon"
        icon={interacting ? hoverIcon : defaultIcon}
        reducedMotion="user"
        size={18}
        spring="snappy"
        strokeWidth={1.7}
      />
      {!collapsed && <span className="nav-text">{label}</span>}
    </NavLink>
  )
}

function Sidebar({ collapsed = false, onMapControlsTargetChange }) {
  const [monitoringExpanded, setMonitoringExpanded] = useState(false)
  const [notificationsActive, setNotificationsActive] = useState(false)
  const { user } = useAuth()
  const location = useLocation()
  const { count: unreadCount, error: unreadError } = useUnreadNotificationCount()
  const canManage = hasCapability(user, 'management.read')
  const canSeeNotifications = isDispatcher(user) || hasCapability(user, 'notifications.read')
  const showSidebarAccount = location.pathname !== '/dashboard'

  const canViewSignals = hasCapability(user, 'signals.read')
  const canViewObjects = hasCapability(user, 'objects.read') || hasCapability(user, 'evidence.read')
  const canViewRequests = hasCapability(user, 'requests.read') || canManage
  const canViewEvidence = hasCapability(user, 'evidence.read')

  return (
    <aside className={`sidebar ${collapsed ? 'collapsed' : ''}`}>
      <div className="sidebar-header">
        {!collapsed && (
          <div className="sidebar-logo">
            <img alt="Знак Москоллектора" src={moscollectorLogo} />
          </div>
        )}
      </div>

      <nav aria-label="Основная навигация" className="sidebar-nav">
        {!collapsed && <div className="sidebar-section-label">MAIN</div>}

        {canManage && (
          <div className={`sidebar-monitoring-group${monitoringExpanded ? ' is-expanded' : ''}`}>
            <div className="sidebar-monitoring-heading">
              <SidebarLink collapsed={collapsed} icon="dashboard" label="Мониторинг" to="/dashboard" />
              {!collapsed && (
                <button
                  aria-controls="sidebar-monitoring-dropdown"
                  aria-expanded={monitoringExpanded}
                  aria-label={monitoringExpanded ? 'Свернуть раздел мониторинга' : 'Развернуть раздел мониторинга'}
                  className="sidebar-monitoring-disclosure"
                  onClick={() => setMonitoringExpanded(expanded => !expanded)}
                  type="button"
                >
                  <MorphIcon
                    aria-hidden="true"
                    icon={monitoringExpanded ? ChevronUp : ChevronDown}
                    reducedMotion="user"
                    size={16}
                    spring="snappy"
                    strokeWidth={1.8}
                  />
                </button>
              )}
            </div>
            <div
              aria-hidden={!monitoringExpanded}
              className={`sidebar-monitoring-dropdown${monitoringExpanded ? ' is-expanded' : ''}`}
              id="sidebar-monitoring-dropdown"
              inert={!monitoringExpanded}
            >
              <div className="sidebar-monitoring-dropdown-inner">
                <NavLink className={({ isActive }) => isActive ? 'sidebar-subnav-link active' : 'sidebar-subnav-link'} to="/dashboard">
                  Карта объектов
                </NavLink>
                <div
                  aria-label="Фильтры и градация карты"
                  className="sidebar-map-controls-slot"
                  ref={onMapControlsTargetChange}
                />
              </div>
            </div>
          </div>
        )}

        {canViewObjects && <SidebarLink collapsed={collapsed} icon="objects" label="Объекты" to="/objects" />}
        {canViewEvidence && <SidebarLink collapsed={collapsed} icon="predictions" label="История прогнозов" to="/predictions" />}
        {canViewRequests && <SidebarLink collapsed={collapsed} icon="maintenance" label="Заявки" to="/maintenance" />}
        {canViewSignals && (
          <SidebarLink
            collapsed={collapsed}
            icon="signals"
            label="Прогнозы"
            to="/signals"
          />
        )}
        {canViewEvidence && <SidebarLink collapsed={collapsed} icon="scenarios" label="Сценарии" to="/scenarios" />}
        {hasCapability(user, 'tasks.execute') && <SidebarLink collapsed={collapsed} icon="maintenance" label="Мои задачи" to="/tasks" />}
        {hasCapability(user, 'access.manage') && <SidebarLink collapsed={collapsed} icon="access" label="Доступ" to="/access" />}
      </nav>

      {showSidebarAccount && <div className="sidebar-bottom sidebar-account-actions">
        <ProfileMenu variant="sidebar" />
        {canSeeNotifications && <NavLink
          aria-label={unreadError ? 'Уведомления, счётчик недоступен' : unreadCount == null ? 'Уведомления, счётчик загружается' : `Уведомления, непрочитанных: ${unreadCount}`}
          className={({ isActive }) => isActive ? 'nav-link sidebar-notification-link active' : 'nav-link sidebar-notification-link'}
          onBlur={() => setNotificationsActive(false)}
          onFocus={() => setNotificationsActive(true)}
          onPointerEnter={() => setNotificationsActive(true)}
          onPointerLeave={() => setNotificationsActive(false)}
          to="/notifications"
        >
          <MorphIcon
            aria-hidden="true"
            className="nav-icon"
            icon={notificationsActive ? BellRing : Bell}
            reducedMotion="user"
            size={20}
            spring="snappy"
            strokeWidth={1.7}
          />
          {unreadCount > 0 && <span aria-hidden="true" className="sidebar-notification-badge">{unreadCount}</span>}
          {unreadError && <span aria-hidden="true" className="sidebar-notification-badge is-error">!</span>}
        </NavLink>}
      </div>}
    </aside>
  )
}

export default Sidebar
