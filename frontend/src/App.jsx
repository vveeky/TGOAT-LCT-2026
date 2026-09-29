import { useEffect, useState } from 'react'
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
} from 'react-router-dom'

import Sidebar from './components/layout/Sidebar'
import Topbar from './components/layout/Topbar'

import DashboardPage from './pages/DashboardPage'
import ObjectsPage from './pages/ObjectsPage'
import ObjectPage from './pages/ObjectPage'
import ChannelPage from './pages/ChannelPage'
import PredictionsPage from './pages/PredictionsPage'
import PredictionPage from './pages/PredictionPage'
import MaintenancePage from './pages/MaintenancePage'
import NotificationsPage from './pages/NotificationsPage'
import ScenarioLabPage from './pages/ScenarioLabPage'
import SignalsPage from './pages/SignalsPage'
import AuthGate from './auth/AuthGate'
import AuthProvider from './auth/AuthProvider'
import EscapeDismissProvider from './hooks/EscapeDismissProvider'
import ReplayRuntimeProvider from './hooks/ReplayRuntimeProvider'
import UnreadNotificationCountProvider from './hooks/UnreadNotificationCountProvider'
import { persistAppearanceTheme, readAppearanceTheme } from './theme'
import { useAuth } from './auth/useAuth'
import RequestExecutionPage from './pages/RequestExecutionPage'
import AccessAdministrationPage from './pages/AccessAdministrationPage'
import { hasCapability, hasRole } from './auth/permissions'

function Landing() {
  const {user}=useAuth()
  const destination = hasCapability(user, 'access.manage') && !hasCapability(user, 'management.read')
    ? '/access' : hasRole(user, 'technician') && !hasCapability(user, 'management.read')
      ? '/tasks' : hasCapability(user, 'management.read') ? '/dashboard' : '/objects'
  return <Navigate to={destination} replace/>
}

function App() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => (
    window.matchMedia('(max-width: 900px)').matches
  ))
  const [mapControlsTarget, setMapControlsTarget] = useState(null)
  const [theme, setTheme] = useState(readAppearanceTheme)

  useEffect(() => {
    const mobileLayout = window.matchMedia('(max-width: 900px)')
    const syncSidebarToViewport = (event) => setSidebarCollapsed(event.matches)
    mobileLayout.addEventListener('change', syncSidebarToViewport)
    return () => mobileLayout.removeEventListener('change', syncSidebarToViewport)
  }, [])

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    persistAppearanceTheme(theme)
  }, [theme])

  return (
    <AuthProvider>
      <AuthGate>
        <BrowserRouter>
          <EscapeDismissProvider>
            <ReplayRuntimeProvider>
              <UnreadNotificationCountProvider>
                <div className="app-layout">
                  <Sidebar collapsed={sidebarCollapsed} onMapControlsTargetChange={setMapControlsTarget} />

                  <div
                    className={`app-main ${
                      sidebarCollapsed ? 'sidebar-collapsed' : ''
                    }`}
                  >
                    <Topbar onThemeChange={setTheme} theme={theme} />

                    <main className="page-content">
                      <Routes>
                        <Route path="/tasks" element={<RequestExecutionPage/>}/>
                        <Route path="/access" element={<AccessAdministrationPage/>}/>
                        <Route path="/signals" element={<SignalsPage />} />
                        <Route
                          path="/"
                          element={<Landing/>}
                        />

                        <Route
                          path="/dashboard"
                          element={<DashboardPage mapControlsTarget={mapControlsTarget} />}
                        />

                        <Route
                          path="/objects"
                          element={<ObjectsPage />}
                        />

                        <Route
                          path="/objects/:id"
                          element={<ObjectPage />}
                        />

                        <Route
                          path="/channels/:id"
                          element={<ChannelPage />}
                        />

                        <Route
                          path="/predictions"
                          element={<PredictionsPage />}
                        />

                        <Route
                          path="/predictions/:id"
                          element={<PredictionPage />}
                        />

                        <Route
                          path="/maintenance"
                          element={<MaintenancePage />}
                        />

                        <Route
                          path="/scenarios"
                          element={<ScenarioLabPage />}
                        />

                        <Route
                          path="/notifications"
                          element={<NotificationsPage />}
                        />
                      </Routes>
                    </main>
                  </div>
                </div>
              </UnreadNotificationCountProvider>
            </ReplayRuntimeProvider>
          </EscapeDismissProvider>
        </BrowserRouter>
      </AuthGate>
    </AuthProvider>
  )
}

export default App
