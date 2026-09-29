import { useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { getMaintenanceRequests, getMaintenanceRequest, getEligibleTechnicians, executeMaintenanceCommand } from '../api/maintenance'
import { useAuth } from '../auth/useAuth'
import { hasRole, isDispatcher } from '../auth/permissions'
import { formatMoscowDateTime } from '../dateTime'
import { WORKFLOW_UPDATED } from '../api/signals'
import AutosizeTextarea from '../components/AutosizeTextarea'

const requestStatusLabels={draft:'Черновик',new:'Новая',assigned:'Назначена',in_progress:'В работе',awaiting_review:'Ожидает приёмки',closed:'Закрыта',cancelled:'Отменена'}
const resultLabels={issue_found:'Обнаружена проблема',no_issue_found:'Проблема не обнаружена',needs_followup:'Требуется дополнительная проверка',unable_to_verify:'Не удалось проверить'}
const priorities={low:'Низкий',normal:'Обычный',medium:'Средний',high:'Высокий',urgent:'Срочный'}
const events={created:'Создана',submitted:'Отправлена',assigned:'Назначен исполнитель',started:'Проверка начата',result_submitted:'Результат отправлен',returned:'Возвращена на доработку',accepted:'Результат принят',cancelled:'Отменена'}

export function RequestDetail({id}) {
  const navigate=useNavigate(), stopped=useRef(false), polling=useRef(null)
  const {user}=useAuth(), dispatcher=isDispatcher(user)
  const [item,setItem]=useState(null),[error,setError]=useState(null),[notice,setNotice]=useState(null)
  const [eligible,setEligible]=useState([]),[assignee,setAssignee]=useState(''),[comment,setComment]=useState('')
  const [summary,setSummary]=useState(''),[code,setCode]=useState(''),[busy,setBusy]=useState(false),[retry,setRetry]=useState(0)
  const pending=useRef(null),editingVersion=useRef(null)
  const edit=()=>{editingVersion.current??=item.version}
  useEffect(()=>{
    let active=true
    const refresh=async()=>{
      if(stopped.current)return
      try {
        const value=await getMaintenanceRequest(id)
        if(active&&!stopped.current){setItem(value);setError(null)}
        if(dispatcher && value.status==='new') {
          try {const users=await getEligibleTechnicians(value.object_id);if(active)setEligible(users)}
          catch {if(active)setNotice('Не удалось загрузить исполнителей. Повторите загрузку.')}
        }
      } catch(e){if(active&&!stopped.current)setError(e.status===404 || e.status===403?'Заявка не найдена или недоступна.':'Не удалось загрузить заявку.')}
    }
    refresh();const timer=setInterval(refresh,10000);polling.current=timer
    return()=>{active=false;clearInterval(timer)}
  },[id,dispatcher,retry])
  async function command(action,fields={}) {
    if(busy)return
    const body={expected_version:editingVersion.current??item.version,...fields}
    const signature=JSON.stringify({action,...body,expected_version:undefined})
    if(pending.current?.signature!==signature)pending.current={signature,body:{...body,idempotency_key:crypto.randomUUID()}}
    setBusy(true);setNotice(null)
    try {
      const result=await executeMaintenanceCommand(id,action,pending.current.body)
      if(!dispatcher && item.status==='draft' && ((action==='submit'&&result.status==='new') || (action==='cancel'&&result.status==='cancelled'))) {
        stopped.current=true;clearInterval(polling.current);pending.current=null
        window.dispatchEvent(new Event(WORKFLOW_UPDATED))
        navigate('/tasks',{replace:true,state:{requestHandoff:action==='submit'?'submitted':'cancelled'}})
        return
      }
      setItem(result)
      pending.current=null;editingVersion.current=null;setSummary('');setComment('');setNotice('Действие сохранено.')
      window.dispatchEvent(new Event(WORKFLOW_UPDATED))
    } catch(e) {
      if(e.status===409) {
        pending.current=null;editingVersion.current=null;setNotice('Заявка изменилась или действие недоступно. Введённый текст сохранён.')
        try {setItem(await getMaintenanceRequest(id))}catch {setNotice('Не удалось обновить заявку. Введённый текст сохранён.')}
      } else setNotice('Не удалось сохранить действие. Введённый текст сохранён; повторите попытку.')
    } finally {setBusy(false)}
  }
  if(error)return <p role="alert">{error} <button onClick={()=>setRetry(v=>v+1)}>Повторить</button></p>
  if(!item)return <p role="status">Загружаем заявку…</p>
  const returned=[...(item.timeline??[])].reverse().find(e=>e.event==='returned')
  const results=[...(item.results??[])].sort((a,b)=>a.revision-b.revision)
  const latestResult=results.at(-1)
  const previousResults=results.slice(0,-1)
  const displayedStatus=returned && item.status==='in_progress'?'Возвращена на доработку':requestStatusLabels[item.status]
  const hasWorkflowActions = item.status === 'draft'
    || (dispatcher && ['new', 'assigned', 'in_progress', 'awaiting_review'].includes(item.status))
    || (!dispatcher && ['assigned', 'in_progress'].includes(item.status))
  return <article className="content-panel request-detail-panel">
    <header className="request-detail-heading">
      <div>
        <span className="request-detail-kicker">Операционный журнал · заявка №{item.id}</span>
        <h1>Заявка №{item.id}</h1>
        <Link className="request-detail-object" to={`/objects/${item.object_id}?request_id=${item.id}`}>
          {item.object?.name ?? `Объект ${item.object_id}`} <span aria-hidden="true">→</span>
        </Link>
      </div>
      <div className="request-detail-badges">
        <span className={`state-pill state-${item.status}${returned && item.status==='in_progress'?' is-returned':''}`}>{displayedStatus}</span>
        <span className={`request-priority-pill priority-${item.priority}`}>{priorities[item.priority]}</span>
      </div>
    </header>

    <dl className="request-detail-summary">
      <div className="request-detail-reason">
        <dt>Причина проверки</dt>
        <dd>{item.reason}</dd>
      </div>
      <div>
        <dt>Исполнитель</dt>
        <dd>{item.assignee_user_id ?? 'Не назначена'}</dd>
      </div>
      {item.assigned_at && <div>
        <dt>Назначил · {item.assigned_by_user_id}</dt>
        <dd>{formatMoscowDateTime(item.assigned_at)}</dd>
      </div>}
      {item.historical_basis && <div>
        <dt>Основание проверки</dt>
        <dd>{item.historical_basis.risk_probability == null
          ? 'Оценка недоступна'
          : `${Math.round(item.historical_basis.risk_probability * 100)}%`}
          <span className="sr-only">Основание проверки на момент решения диспетчера: {item.historical_basis.risk_probability == null
            ? 'Оценка недоступна'
            : `${Math.round(item.historical_basis.risk_probability * 100)}%`}</span>
          <span>{formatMoscowDateTime(item.historical_basis.snapshot_ts)}</span>
        </dd>
      </div>}
      {item.object_current && <div className="request-detail-current-score">
        <dt><span className="sr-only">Текущая оценка объекта: </span>Текущий риск</dt>
        <dd>{item.object_current.prediction_status === 'available' && Number.isFinite(item.object_current.risk_probability)
          ? `${Math.round(item.object_current.risk_probability * 100)}%`
          : 'Оценка недоступна'}
          <span className="sr-only">Текущая оценка объекта: {item.object_current.prediction_status === 'available' && Number.isFinite(item.object_current.risk_probability)
            ? `${Math.round(item.object_current.risk_probability * 100)}%`
            : 'Оценка недоступна'}</span>
        </dd>
      </div>}
    </dl>

    {(notice || item.status === 'awaiting_review') && <p className="request-detail-notice" role={notice ? 'status' : undefined}>
      {notice ?? (dispatcher ? 'Результат ожидает приёмки.' : 'Результат отправлен диспетчеру. Ожидает решения диспетчера.')}
    </p>}
    {item.status === 'closed' && <p className="request-detail-notice">
      Принято: {item.accepted_by_user_id} · {formatMoscowDateTime(item.accepted_at)}
    </p>}

    <section className={`request-detail-actions${hasWorkflowActions ? '' : ' is-empty'}`} aria-label="Действия по заявке">
      {dispatcher && item.status === 'new' && <div className="request-detail-assign">
        <label>Исполнитель <select value={assignee} onChange={e => { edit(); setAssignee(e.target.value) }}>
          <option value="">Выберите исполнителя</option>
          {eligible.map(u => <option key={u.user_id} value={u.user_id}>{u.username}</option>)}
        </select></label>
        <button disabled={busy || !assignee} onClick={() => command('assign', { assignee_user_id: assignee })}>Назначить</button>
      </div>}
      {!dispatcher && item.status === 'assigned' && <button disabled={busy} onClick={() => command('start')}>Начать проверку</button>}
      {!dispatcher && item.status === 'in_progress' && <form onSubmit={e => { e.preventDefault(); command('result', { result_code: code, summary: summary.trim() }) }}>
        {returned && <p className="request-detail-returned">Требуется доработка: {returned.comment}</p>}
        <label>Результат проверки <select required value={code} onChange={e => { edit(); setCode(e.target.value) }}>
          <option value="" disabled>Выберите результат</option>
          {Object.entries(resultLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select></label>
        {!code && <p className="field-validation-message" role="status">Выберите результат проверки перед отправкой.</p>}
        <label>Описание результата <AutosizeTextarea required value={summary} onChange={e => { edit(); setSummary(e.target.value) }} /></label>
        <button disabled={busy || !code || !summary.trim()}>Отправить результат</button>
      </form>}
      {dispatcher && ['new', 'assigned', 'in_progress', 'awaiting_review'].includes(item.status) && <label className="request-detail-comment">
        Комментарий диспетчера <AutosizeTextarea value={comment} onChange={e => { edit(); setComment(e.target.value) }} />
      </label>}
      {dispatcher && item.status === 'awaiting_review' && <div className="request-detail-button-row">
        <button disabled={busy} onClick={() => command('accept', { comment })}>Принять результат</button>
        <button className="request-detail-secondary-action" disabled={busy || !comment.trim()} onClick={() => command('return', { comment })}>Вернуть на доработку</button>
      </div>}
      {dispatcher && ['new', 'assigned', 'in_progress'].includes(item.status) && <button className="request-detail-secondary-action" disabled={busy || !comment.trim()} onClick={() => command('cancel', { comment })}>Отменить заявку</button>}
      {item.status === 'draft' && <>
        <button disabled={busy} onClick={() => command('submit')}>Отправить заявку</button>
        <label className="request-detail-comment">Причина отмены<AutosizeTextarea value={comment} onChange={e => { edit(); setComment(e.target.value) }} /></label>
        <button className="request-detail-secondary-action" disabled={busy || !comment.trim()} onClick={() => command('cancel', { comment })}>Отменить черновик</button>
      </>}
    </section>

    {latestResult && <section className="request-detail-result">
      <h2>Последний результат проверки · версия {latestResult.revision}</h2>
      <p>{resultLabels[latestResult.result_code]}</p>
      <p>{latestResult.summary}</p>
      <p className="request-detail-result-meta">{latestResult.technician_user_id} · {formatMoscowDateTime(latestResult.submitted_at)}</p>
    </section>}
    {previousResults.length > 0 && <details className="request-detail-history">
      <summary>Предыдущие результаты · {previousResults.length}</summary>
      {previousResults.map(result => <section className="request-detail-result" key={result.id}>
        <h3>Версия {result.revision} · {resultLabels[result.result_code]}</h3>
        <p>{result.summary}</p>
        <p className="request-detail-result-meta">{result.technician_user_id} · {formatMoscowDateTime(result.submitted_at)}</p>
      </section>)}
    </details>}
    <details className="request-detail-history">
      <summary>История заявки</summary>
      <ul>{(item.timeline ?? []).map(e => <li key={e.id}>{events[e.event]} · {e.actor_user_id} · {formatMoscowDateTime(e.timestamp)} {e.comment}</li>)}</ul>
    </details>
  </article>
}

export default function RequestExecutionPage() {
  const location=useLocation(),navigate=useNavigate()
  const handoff=location.state?.requestHandoff
  const [flash,setFlash]=useState(handoff??null)
  if((handoff==='submitted'||handoff==='cancelled') && flash!==handoff)setFlash(handoff)
  useEffect(()=>{
    const handoff=location.state?.requestHandoff
    if(handoff==='submitted'||handoff==='cancelled') {
      navigate(location.pathname+location.search,{replace:true,state:null})
    }
  },[location,navigate])
  const {user}=useAuth(),[params]=useSearchParams(),id=params.get('request_id')
  const [filter,setFilter]=useState(''),[data,setData]=useState(null),[error,setError]=useState(null),[retry,setRetry]=useState(0)
  const [offset,setOffset]=useState(0),[objectFilter,setObjectFilter]=useState(''),[assigneeFilter,setAssigneeFilter]=useState(''),[priorityFilter,setPriorityFilter]=useState('')
  const technician=hasRole(user,'technician') && !isDispatcher(user)
  const newAssignmentCount=technician?(data?.items??[]).filter(item=>item.latest_event==='assigned'&&item.status==='assigned').length:0
  const returnedTaskCount=technician?(data?.items??[]).filter(item=>item.latest_event==='returned'&&item.status==='in_progress').length:0
  useEffect(()=>{
    if(id)return
    let active=true
    const refresh=()=>getMaintenanceRequests({status:filter,limit:100,offset,object_id:objectFilter,assignee_user_id:assigneeFilter,priority:priorityFilter,active_only:technician && !filter}).then(value=>{if(active){setData(value);setError(null)}}).catch(()=>{if(active)setError('Не удалось загрузить заявки.')})
    refresh();const timer=setInterval(refresh,10000)
    return()=>{active=false;clearInterval(timer)}
  },[id,filter,retry,offset,objectFilter,assigneeFilter,priorityFilter,technician])
  return (
    <section className="content-page request-execution-page">
      <header className="content-page-heading">
        <h1>{technician ? 'Мои задачи' : 'Заявки'}</h1>
      </header>
      {(flash === 'submitted' || flash === 'cancelled') && (
        <p role="status">{flash === 'submitted' ? 'Заявка отправлена диспетчеру на назначение.' : 'Черновик отменён.'}</p>
      )}
      {id ? <><Link className="request-detail-back-link" to={technician ? '/tasks' : '/maintenance'}>← К списку</Link><RequestDetail key={id} id={id} /></> : <>
        <section className="request-filter-panel" aria-label="Фильтры заявок">
          <div className="request-filter-heading">
            <span>ФИЛЬТРЫ</span>
            <Link className="request-create-link" to="/maintenance?create=1">
              Создать заявку вручную <span aria-hidden="true">→</span>
            </Link>
          </div>
          <div className={`request-filter-grid${technician ? ' is-technician' : ''}`}>
            <label>
              Статус
              <select value={filter} onChange={e => { setFilter(e.target.value); setOffset(0); setData(null) }}>
                <option value="">{technician ? 'Все текущие задачи' : 'Все'}</option>
                {Object.entries(requestStatusLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
              </select>
            </label>
            {!technician && <>
              <label>
                Объект (номер)
                <input type="number" min="1" value={objectFilter} onChange={e => { setObjectFilter(e.target.value); setOffset(0); setData(null) }} />
              </label>
              <label>
                Исполнитель (логин)
                <input value={assigneeFilter} onChange={e => { setAssigneeFilter(e.target.value); setOffset(0); setData(null) }} />
              </label>
              <label>
                Приоритет
                <select value={priorityFilter} onChange={e => { setPriorityFilter(e.target.value); setOffset(0); setData(null) }}>
                  <option value="">Любой</option>
                  {Object.entries(priorities).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                </select>
              </label>
            </>}
          </div>
        </section>
        {error && <p role="alert">{error} <button onClick={() => setRetry(v => v + 1)}>Повторить</button></p>}
        {!data && !error && <p role="status">Загружаем заявки…</p>}
        {newAssignmentCount>0 && <p className="task-event-notice is-assigned" role="status">Новое назначение: {newAssignmentCount}</p>}
        {returnedTaskCount>0 && <p className="task-event-notice is-returned" role="status">Возвращена на доработку: {returnedTaskCount}</p>}
        {data && !error && <>
          <div className="request-results-heading" role="status">
            <h2>Список заявок</h2>
            <span>Заявок: {data.total}</span>
          </div>
          {data.total === 0 && <p className="request-empty-state">{technician && !filter ? 'Сейчас назначенных вам задач нет.' : 'Задач по выбранным фильтрам нет.'}</p>}
          <ul className="record-list request-record-list">
            {data.items.map(item => {
              const returned=item.latest_event==='returned'&&item.status==='in_progress'
              const assigned=technician&&item.latest_event==='assigned'&&item.status==='assigned'
              const statusLabel=returned?'Возвращена на доработку':assigned?'Новое назначение':requestStatusLabels[item.status]
              return (
                <li className={`record-card request-record${item.status==='awaiting_review'?' is-awaiting-review':''}${returned?' is-returned':''}${assigned?' is-new-assignment':''}`} key={item.id}>
                <header className="request-record-heading">
                  <div className="request-record-title">
                    <span className="request-record-id">Заявка №{item.id}</span>
                    <h3>{item.object_name ?? 'Название объекта недоступно'}</h3>
                  </div>
                  <div className="request-record-badges">
                    <span className={`state-pill state-${item.status}${returned?' is-returned':''}${assigned?' is-new-assignment':''}`}>{statusLabel}</span>
                    <span className={`request-priority-pill priority-${item.priority}`}>{priorities[item.priority]}</span>
                  </div>
                </header>
                <p className="request-record-reason">{item.reason}</p>
                <dl className="request-record-meta">
                  <div>
                    <dt>Исполнитель</dt>
                    <dd>{item.assignee_user_id ?? 'Не назначена'}</dd>
                  </div>
                  <div>
                    <dt>Обновлено</dt>
                    <dd>{formatMoscowDateTime(item.updated_at)}</dd>
                  </div>
                </dl>
                <Link className="request-record-open" to={`?request_id=${item.id}`}>
                  Открыть заявку <span aria-hidden="true">→</span>
                </Link>
                </li>
              )
            })}
          </ul>
        </>}
        {data && data.total > 100 && <nav aria-label="Страницы заявок"><button disabled={offset === 0} onClick={() => { setOffset(v => Math.max(0, v - 100)); setData(null) }}>Назад</button><span>{offset + 1}–{Math.min(offset + 100, data.total)} из {data.total}</span><button disabled={offset + 100 >= data.total} onClick={() => { setOffset(v => v + 100); setData(null) }}>Далее</button></nav>}
      </>}
    </section>
  )
}
