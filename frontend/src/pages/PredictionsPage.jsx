import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { getObjectCatalog } from '../api/objects'
import { getUserHistory } from '../api/operational'
import { useReplayRuntime } from '../hooks/useReplayRuntime'
import { formatMoscowDateTime } from '../dateTime'
import { decisionLabels,signalLabels } from '../api/signals'
import { useAuth } from '../auth/useAuth'
import { isDispatcher } from '../auth/permissions'

const taskLabels={new:'Новая',assigned:'Назначена',in_progress:'В работе',awaiting_review:'Ожидает приёмки',closed:'Закрыта',cancelled:'Отменена'}
export default function PredictionsPage(){
  const {user}=useAuth(),dispatcher=isDispatcher(user)
  const [params]=useSearchParams()
  const [objectId,setObjectId]=useState(params.get('object_id')??''),[period,setPeriod]=useState(''),[state,setState]=useState(''),[offset,setOffset]=useState(0)
  const [catalog,setCatalog]=useState([]),[data,setData]=useState(null),[error,setError]=useState(null),[retry,setRetry]=useState(0)
  const {status:replayStatus}=useReplayRuntime()
  useEffect(()=>{let active=true;getObjectCatalog().then(value=>{if(active)setCatalog(value.items)}).catch(()=>{});return()=>{active=false}},[])
  useEffect(()=>{
    let active=true
    const refresh=()=>{const filter={object_id:objectId,state,limit:50,offset};if(period){const end=replayStatus?.mode==='replay'&&replayStatus.virtual_time?new Date(replayStatus.virtual_time):new Date();filter.to=end.toISOString();filter.from=new Date(end.getTime()-Number(period)*86400000).toISOString()};return getUserHistory(filter).then(value=>{if(active){setData(value);setError(null)}}).catch(()=>{if(active)setError('Не удалось загрузить историю.')})}
    refresh();const timer=setInterval(refresh,replayStatus?.mode === 'replay' ? 1500 : 30_000)
    return()=>{active=false;clearInterval(timer)}
  },[objectId,period,state,offset,retry,replayStatus?.mode,replayStatus?.session_id,replayStatus?.virtual_time])
  return <section className="content-page history-page"><header className="content-page-heading"><div><span className="page-eyebrow">Оценки и решения</span><h1>История</h1><p>Изменение оценки риска и работа по связанным сигналам</p></div>{data&&<span className="page-total">Записей: {data.total}</span>}</header>
    <div className="page-filter-toolbar history-filter-toolbar"><label>Объект <select value={objectId} onChange={e=>{setObjectId(e.target.value);setOffset(0);setData(null)}}><option value="">Все объекты</option>{catalog.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    <label>Период <select value={period} onChange={e=>{setPeriod(e.target.value);setOffset(0);setData(null)}}><option value="">Последние расчёты</option><option value="1">24 часа</option><option value="7">7 дней</option><option value="30">30 дней</option></select></label>
    {dispatcher&&<label>Работа по сигналу <select value={state} onChange={e=>{setState(e.target.value);setOffset(0);setData(null)}}><option value="">Все состояния</option>{Object.entries(signalLabels).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>}</div>
    {error&&<p role="alert">{error} <button onClick={()=>setRetry(x=>x+1)}>Повторить</button></p>}
    {!data&&!error&&<p role="status">Загружаем историю…</p>}
    {data&&!error&&<>{!data.items.length&&<p className="page-state page-state-empty">За выбранный период записей по этим условиям нет.</p>}<ul className="record-list history-record-list">{data.items.map(item=><li className="record-card history-record" key={item.identity}><div className="history-record-heading"><div><span className="record-id">Объект</span><h2><Link to={`/objects/${item.object_id}${item.card_id?`?signal=${encodeURIComponent(item.card_id)}`:''}`}>{item.object_name}</Link></h2></div><strong className="record-score">{item.prediction_status==='available'&&Number.isFinite(item.risk_probability)?`${Math.round(item.risk_probability*100)}%`:'Оценка недоступна'}</strong></div><time className="history-record-time">Расчёт от {formatMoscowDateTime(item.snapshot_ts)}</time><div className="record-card-meta">{item.signal_state&&<span className={`state-pill state-${item.signal_state}`}>{signalLabels[item.signal_state]}</span>}{item.decision&&<span className="state-pill state-info">{decisionLabels[item.decision]??'Решение зарегистрировано'}</span>}{item.request_id&&<span className={`state-pill state-${item.request_status}`}>{taskLabels[item.request_status]??'Состояние уточняется'}</span>}</div><div className="record-card-actions">{item.request_id&&<Link to={`${dispatcher?"/maintenance":"/tasks"}?request_id=${item.request_id}`}>Связанная заявка →</Link>}<Link to={`/predictions/${encodeURIComponent(item.identity)}`}>Открыть расчёт →</Link></div></li>)}</ul>{data.total>50&&<nav className="pagination" aria-label="Страницы истории"><button disabled={!offset} onClick={()=>{setOffset(x=>Math.max(0,x-50));setData(null)}}>Новее</button><span>{offset+1}–{Math.min(offset+50,data.total)} из {data.total}</span><button disabled={offset+50>=data.total} onClick={()=>{setOffset(x=>x+50);setData(null)}}>Старее</button></nav>}</>}
  </section>
}
