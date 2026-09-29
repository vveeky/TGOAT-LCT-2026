import { useEffect, useState } from 'react'

import { getAccessPrincipals, updateAccessPrincipal } from '../api/access'
import { hasCapability } from '../auth/permissions'
import { useAuth } from '../auth/useAuth'
import AutosizeTextarea from '../components/AutosizeTextarea'

const roles = [
  ['central_dispatcher', 'Диспетчер ОДС'],
  ['district_dispatcher', 'Окружной диспетчер'],
  ['technician', 'Техник'],
]
const grants = [
  ['management.read', 'Руководитель: сводка и работы'],
  ['evidence.read', 'Аналитик: данные и история'],
  ['model.explain', 'Аналитик: объяснения модели'],
]

function PrincipalEditor({ principal, onSaved }) {
  const [selectedRoles, setSelectedRoles] = useState(principal.roles)
  const [selectedGrants, setSelectedGrants] = useState(principal.grants.filter(value => grants.some(([key]) => key === value)))
  const [groups, setGroups] = useState(principal.groups.join('\n'))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const toggle = (values, setter, value) => setter(values.includes(value)
    ? values.filter(item => item !== value) : [...values, value])

  async function save(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const result = await updateAccessPrincipal(principal.user_id, {
        roles: selectedRoles,
        grants: selectedGrants,
        groups: groups.split(/\r?\n/).map(value => value.trim()).filter(Boolean),
        expected_version: principal.version,
      })
      onSaved(result)
    } catch (nextError) {
      setError(nextError.status === 409
        ? 'Права уже изменены другим администратором. Обновите список и повторите.'
        : nextError.message)
    } finally {
      setBusy(false)
    }
  }

  return <form className="content-panel" onSubmit={save}>
    <h2>{principal.username}</h2>
    <p>Версия прав: {principal.version}</p>
    <fieldset><legend>Рабочие роли</legend>{roles.map(([value, label]) =>
      <label key={value}><input type="checkbox" checked={selectedRoles.includes(value)}
        onChange={() => toggle(selectedRoles, setSelectedRoles, value)} />{label}</label>)}</fieldset>
    <fieldset><legend>Дополнительные права на просмотр</legend>{grants.map(([value, label]) =>
      <label key={value}><input type="checkbox" checked={selectedGrants.includes(value)}
        onChange={() => toggle(selectedGrants, setSelectedGrants, value)} />{label}</label>)}</fieldset>
    <label>Группы доступа, по одной в строке: complex:28 или district:9
      <AutosizeTextarea value={groups} onChange={event => setGroups(event.target.value)} />
    </label>
    {error && <p role="alert">{error}</p>}
    <button disabled={busy} type="submit">{busy ? 'Сохраняю…' : 'Сохранить права'}</button>
  </form>
}

export default function AccessAdministrationPage() {
  const { user } = useAuth()
  const [principals, setPrincipals] = useState(null)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState('')

  const refresh = () => getAccessPrincipals().then(items => {
    setPrincipals(items)
    setError(null)
  }).catch(nextError => setError(nextError.message))

  useEffect(() => { if (hasCapability(user, 'access.manage')) refresh() }, [user])

  if (!hasCapability(user, 'access.manage')) return <section className="content-page"><h1>Доступ запрещён</h1></section>
  const principal = principals?.find(item => item.user_id === selected)
  return <section className="content-page">
    <header className="content-page-heading"><h1>Управление доступом</h1></header>
    <p>Роли складываются. Рабочие действия и область объектов проверяются сервером.</p>
    {error && <p role="alert">{error} <button onClick={refresh}>Повторить</button></p>}
    {!principals && !error && <p role="status">Загружаю пользователей…</p>}
    {principals && <label>Пользователь <select value={selected} onChange={event => setSelected(event.target.value)}>
      <option value="">Выберите пользователя</option>
      {principals.filter(item => item.user_id !== user.user_id).map(item =>
        <option key={item.user_id} value={item.user_id}>{item.username}</option>)}
    </select></label>}
    {principal && <PrincipalEditor key={`${principal.user_id}:${principal.version}`} principal={principal}
      onSaved={saved => setPrincipals(items => items.map(item => item.user_id === saved.user_id ? saved : item))} />}
  </section>
}
