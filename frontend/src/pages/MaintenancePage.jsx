import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { createMaintenanceRequest, getMaintenanceRequest, getMaintenanceRequests, executeMaintenanceCommand } from '../api/maintenance'
import { getObjects } from '../api/objects'
import { useAuth } from '../auth/useAuth'
import { formatMoscowDateTime } from '../dateTime'
import RequestExecutionPage from './RequestExecutionPage'
import { hasCapability } from '../auth/permissions'
import AutosizeTextarea from '../components/AutosizeTextarea'

const PAGE_SIZE = 100
const statusLabels = {
  draft: 'Черновик',
  new: 'Новая',
  assigned: 'Назначена',
  in_progress: 'В работе',
  closed: 'Закрыта',
  cancelled: 'Отменена',
}
const priorities = [
  ['low', 'Низкий'],
  ['normal', 'Обычный'],
  ['medium', 'Средний'],
  ['high', 'Высокий'],
  ['urgent', 'Срочный'],
]

function ManualMaintenancePage() {
  const { user } = useAuth()
  const [searchParams] = useSearchParams()
  const requestedObjectId = searchParams.get('object_id') ?? ''
  const requestedId = searchParams.get('request_id')
  const [objects, setObjects] = useState([])
  const [page, setPage] = useState({ offset: null, items: [], total: 0, error: null })
  const [offset, setOffset] = useState(0)
  const [objectId, setObjectId] = useState(requestedObjectId)
  const [objectSearch, setObjectSearch] = useState('')
  const [reason, setReason] = useState('')
  const [priority, setPriority] = useState('medium')
  const [actionError, setActionError] = useState(null)
  const [retryKey, setRetryKey] = useState(0)
  const [busy, setBusy] = useState(null)
  const loading = page.offset !== offset
  const items = page.offset === offset ? page.items : []
  const total = page.offset === offset ? page.total : 0
  const pageError = page.offset === offset ? page.error : null

  const canCreate = hasCapability(user, 'requests.create')
  const canUpdate = hasCapability(user, 'requests.dispatch')

  const load = useCallback(async (nextOffset) => {
    return Promise.all([
      getObjects({ limit: 1000 }),
      requestedId ? getMaintenanceRequest(requestedId).then(item => ({items:[item],total:1})) : getMaintenanceRequests({ limit: PAGE_SIZE, offset: nextOffset }),
    ])
  }, [requestedId])

  useEffect(() => {
    let active = true
    load(offset)
      .then(([objectResult, requestResult]) => {
        if (!active) return
        setObjects(objectResult.items ?? [])
        setPage({
          offset,
          items: requestResult.items ?? [],
          total: requestResult.total ?? 0,
          error: null,
        })
      })
      .catch((nextError) => {
        if (active) setPage({ offset, items: [], total: 0, error: nextError.message })
      })
    return () => { active = false }
  }, [load, offset, retryKey])

  async function submit(event) {
    event.preventDefault()
    if (!canCreate || !objectId || !reason.trim()) return
    setBusy('create')
    setActionError(null)
    try {
      await createMaintenanceRequest({
        object_id: Number(objectId),
        channel_id: null,
        prediction_id: null,
        reason: reason.trim(),
        priority,
      })
      setReason('')
      setOffset(0)
      const [objectResult, requestResult] = await load(0)
      setObjects(objectResult.items ?? [])
      setPage({ offset: 0, items: requestResult.items ?? [], total: requestResult.total ?? 0, error: null })
    } catch (nextError) {
      setActionError(nextError.message)
    } finally {
      setBusy(null)
    }
  }

  async function changeStatus(item, status) {
    if (!canUpdate || item.status === status) return
    setBusy(item.id)
    setActionError(null)
    try {
      await executeMaintenanceCommand(item.id, 'submit', {expected_version:item.version,idempotency_key:crypto.randomUUID()})
      const [objectResult, requestResult] = await load(offset)
      setObjects(objectResult.items ?? [])
      setPage({ offset, items: requestResult.items ?? [], total: requestResult.total ?? 0, error: null })
    } catch (nextError) {
      setActionError(nextError.message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="content-page maintenance-page">
      <header className="content-page-heading">
        <div>
          <span className="page-eyebrow">Операционная работа</span>
          <h1>Заявки</h1>
          <p>Внешняя система заказчика пока не подключена.</p>
        </div>
        {page.offset === offset && <span className="page-total">Заявок: {total}</span>}
      </header>

      {actionError && <p className="page-state page-state-error" role="alert">Не удалось выполнить операцию: {actionError}</p>}
      {pageError && <p className="page-state page-state-error" role="alert">Не удалось загрузить заявки или список объектов: {pageError}{' '}<button type="button" onClick={() => setRetryKey((key) => key + 1)}>Повторить загрузку</button></p>}

      {requestedId && <Link to="/maintenance">← Все заявки</Link>}
      {canCreate && !requestedId && (
        <form className="content-panel maintenance-form" onSubmit={submit}>
          <div className="content-panel-heading">
            <div>
              <span className="page-eyebrow">Новая запись</span>
              <h2>+ Создать заявку вручную</h2>
            </div>
            <span className="state-pill state-info">Не отправляется заказчику</span>
          </div>
          <div className="form-grid">
          <label>
            Объект
            <input type="search" placeholder="Поиск объекта по названию" value={objectSearch} onChange={event => { setObjectSearch(event.target.value); setObjectId('') }} />
            {objectSearch.trim() && <ul aria-label="Найденные объекты">{objects.filter(object => object.name.toLocaleLowerCase().includes(objectSearch.toLocaleLowerCase())).slice(0, 10).map(object =>
              <li key={object.id}><button type="button" onClick={() => { setObjectId(String(object.id)); setObjectSearch('') }}>{object.name}</button></li>)}</ul>}
            {objectId && <span>Выбран: {objects.find(object => String(object.id) === objectId)?.name ?? 'Объект'}</span>}
          </label>
          <label>
            Причина
            <AutosizeTextarea required minLength={8} value={reason} onChange={(event) => setReason(event.target.value)} />
          </label>
          <label>
            Приоритет
            <select value={priority} onChange={(event) => setPriority(event.target.value)}>
              {priorities.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          </div>
          <button disabled={busy === 'create' || !objectId || !reason.trim()} type="submit">
            {busy === 'create' ? 'Сохраняю…' : 'Сохранить черновик'}
          </button>
        </form>
      )}

      <div className="content-panel maintenance-journal">
      <div className="content-panel-heading">
        <h2>Журнал заявок</h2>
        {loading && <span className="inline-status">Обновление…</span>}
      </div>
      {loading && <p className="page-state" role="status">Загружаю заявки…</p>}
      {!loading && !pageError && items.length === 0 && <p className="page-state page-state-empty">Заявок пока нет.</p>}
      <ul className="record-list maintenance-list">
        {items.map((item) => {
          const object = objects.find((candidate) => candidate.id === item.object_id)
          return (
            <li className="record-card maintenance-record" key={item.id} id={`request-${item.id}`}>
              <div className="record-card-heading">
                <div><span className="record-id">Обновлено · {formatMoscowDateTime(item.updated_at)}</span><h3>Заявка {item.id}</h3></div>
                <span className={`state-pill state-${item.status}`}>{statusLabels[item.status] ?? 'Статус уточняется'}</span>
              </div>
              <p>
              {item.object_id != null
                ? <Link to={`/objects/${item.object_id}`}>{object?.name ?? `Объект ${item.object_id}`}</Link>
                : 'Объект не указан'}
              {' · '}{item.reason} · Приоритет: {priorities.find(([value]) => value === item.priority)?.[1] ?? 'Не указан'}
              </p>
              <p>Создана: {formatMoscowDateTime(item.created_at)} · {item.origin === 'signal' ? 'Создана по сигналу' : 'Создана вручную'}</p>
              {item.comment && <p>{item.comment}</p>}
              {item.card_id && <Link to={`/objects/${item.object_id}?signal=${encodeURIComponent(item.card_id)}`}>Открыть связанный сигнал</Link>}
              {canUpdate && item.origin !== 'signal' && item.status === 'draft' && <button disabled={busy === item.id} onClick={() => changeStatus(item, 'new')}>Отправить заявку на проверку</button>}
            </li>
          )
        })}
      </ul>
      {!loading && total > PAGE_SIZE && (
        <nav className="pagination" aria-label="Страницы журнала заявок">
          <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
            Новее
          </button>
          <span> {offset + 1}–{Math.min(offset + items.length, total)} из {total} </span>
          <button type="button" disabled={offset + items.length >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>
            Старее
          </button>
        </nav>
      )}
      </div>
      {!canCreate && <p>Создание заявок недоступно для текущей роли.</p>}
      {!canUpdate && hasCapability(user, 'tasks.execute') && <p>Менять статус заявки может только диспетчер.</p>}
    </section>
  )
}

export default function MaintenancePage() {
  const [params]=useSearchParams()
  return params.get('create')==='1'?<ManualMaintenancePage/>:<RequestExecutionPage/>
}
