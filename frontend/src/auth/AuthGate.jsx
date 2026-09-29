import { useState } from 'react'

import loginBackground from '../assets/login-background-soft-blue.png'
import { useAuth } from './useAuth'

function PasswordVisibilityIcon({ visible }) {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2"
      viewBox="0 0 24 24"
    >
      {visible ? (
        <>
          <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" />
          <circle cx="12" cy="12" r="3" />
        </>
      ) : (
        <>
          <path d="m3 3 18 18" />
          <path d="M10.6 10.6a2 2 0 0 0 2.8 2.8" />
          <path d="M9.9 5.2A10.8 10.8 0 0 1 12 5c6.5 0 10 7 10 7a14.7 14.7 0 0 1-3.2 4.1" />
          <path d="M6.2 6.2A15.3 15.3 0 0 0 2 12s3.5 7 10 7a10.8 10.8 0 0 0 2.1-.2" />
        </>
      )}
    </svg>
  )
}

function LoginScreen({ sessionError, onRetrySession }) {
  const { login } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event) {
    event.preventDefault()
    setSubmitting(true)
    setError(null)

    try {
      await login(username.trim(), password)
    } catch (nextError) {
      setError(nextError.message || 'Не удалось войти')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="auth-screen">
      <section aria-label="Вход в систему Москоллектора" className="auth-layout">
        <div className="auth-visual">
          <img
            alt="Светло-голубая иллюстрация городской инфраструктуры и трубопроводов"
            className="auth-visual-image"
            src={loginBackground}
          />
          <div aria-hidden="true" className="auth-visual-overlay" />
        </div>

        <div className="auth-form-panel">
          <form className="auth-card" onSubmit={handleSubmit}>
            <header className="auth-heading">
              <h1>Вход в систему</h1>
              <p>Войдите с учётной записью диспетчерской службы.</p>
            </header>

            <div className="auth-form-content">
              <div className="auth-form-content-inner">
                {sessionError && (
                  <div className="auth-error" role="alert">
                    Не удалось проверить сохранённую сессию: {sessionError.message || 'ошибка API'}
                    <button className="auth-retry" type="button" onClick={onRetrySession}>
                      Повторить проверку API
                    </button>
                  </div>
                )}

                <label className="auth-field" htmlFor="auth-username">
                  Логин
                  <input
                    autoComplete="username"
                    id="auth-username"
                    required
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                  />
                </label>

                <div className="auth-field">
                  <div className="auth-field-heading">
                    <label htmlFor="auth-password">Пароль</label>
                    <span className="auth-forgot-password">Забыли пароль?</span>
                  </div>
                  <span className="auth-password-wrap">
                    <input
                      autoComplete="current-password"
                      id="auth-password"
                      required
                      type={showPassword ? 'text' : 'password'}
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                    />
                    <button
                      aria-label={showPassword ? 'Скрыть пароль' : 'Показать пароль'}
                      aria-pressed={showPassword}
                      className="auth-password-toggle"
                      onClick={() => setShowPassword((visible) => !visible)}
                      type="button"
                    >
                      <PasswordVisibilityIcon visible={showPassword} />
                    </button>
                  </span>
                </div>

                {error && <div className="auth-error" role="alert">{error}</div>}

                <button className="auth-submit" disabled={submitting} type="submit">
                  {submitting ? 'Проверяем…' : 'Войти'}
                </button>

                <p className="auth-access-note">
                  Впервые здесь? <span>Создайте аккаунт.</span>
                </p>
                <p className="auth-copyright">© 2026 Москоллектор · Демо-версия</p>
              </div>
            </div>
          </form>
        </div>
      </section>
    </main>
  )
}

function AuthGate({ children }) {
  const { loading, user, error: sessionError, refresh } = useAuth()

  if (loading) {
    return <main className="auth-screen">Подключаемся к API…</main>
  }

  if (!user) {
    return <LoginScreen sessionError={sessionError} onRetrySession={refresh} />
  }

  return children
}

export default AuthGate
