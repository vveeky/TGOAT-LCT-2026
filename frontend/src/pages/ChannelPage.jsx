import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { getChannel, getChannelHistory } from '../api/channels'
import { getObject } from '../api/objects'
import { formatMoscowDateTime, moscowWallTimeToUTC } from '../dateTime'
import { useReplayRuntime } from '../hooks/useReplayRuntime'

export default function ChannelPage(){
  const {id}=useParams()
  const {status:replay}=useReplayRuntime()
  const virtualTime=replay?.mode==='replay'?replay.virtual_time:null
  const session=replay?.session_id??'normal'
  const [metadata,setMetadata]=useState(null),[error,setError]=useState(null),[object,setObject]=useState(null),[errorStatus,setErrorStatus]=useState(null)
  const [period,setPeriod]=useState('7'),[alarm,setAlarm]=useState(false),[offset,setOffset]=useState(0)
  const [from,setFrom]=useState(''),[to,setTo]=useState(''),[history,setHistory]=useState(null),[historyError,setHistoryError]=useState(null),[retry,setRetry]=useState(0)
  useEffect(()=>{let active=true;getChannel(id).then(channel=>{if(active){setMetadata({id,session,channel});setError(null);setErrorStatus(null)};return getObject(channel.object_id)}).then(value=>{if(active)setObject(value)}).catch(e=>{if(active){setErrorStatus(e.status);setError(e.status===403||e.status===404?'Канал не найден или недоступен в вашей области доступа.':'Не удалось загрузить сведения о канале.')}});return()=>{active=false}},[id,retry,session,virtualTime])
  const channel=metadata?.id===id&&metadata.session===session?metadata.channel:null
  const visibleHistory=history?.session===session&&history?.virtualTime===virtualTime?history:null
  useEffect(()=>{
    if(!channel)return
    let active=true
    if(replay?.mode==='replay'&&!virtualTime)return
    const end=virtualTime?new Date(virtualTime):channel.last_event_at?new Date(channel.last_event_at):new Date()
    const params={limit:50,offset,alarm_only:alarm}
    if(period==='custom'){
      if(!from||!to)return
      try{params.from=moscowWallTimeToUTC(from);params.to=moscowWallTimeToUTC(to)}catch{Promise.resolve().then(()=>{if(active)setHistoryError('Укажите корректные дату и время в МСК.')});return()=>{active=false}}
      if(params.from>params.to){Promise.resolve().then(()=>{if(active)setHistoryError('Начало периода должно быть раньше окончания.')});return()=>{active=false}}
    }else{params.to=end.toISOString();params.from=new Date(end.getTime()-Number(period)*86400000).toISOString()}
    getChannelHistory(id,params).then(value=>{if(active){setHistory({events:value.events??[],offset,session,virtualTime});setHistoryError(null)}}).catch(()=>{if(active)setHistoryError('Не удалось загрузить историю событий.')})
    return()=>{active=false}
  },[channel,id,period,alarm,offset,from,to,retry,virtualTime,replay?.mode,session])
  const changePeriod=value=>{setPeriod(value);setOffset(0);setHistory(null);setHistoryError(null)}
  if(error&&!channel)return <section className="content-page"><Link to="/objects">К объектам</Link><p role="alert">{errorStatus === 403 || errorStatus === 404 ? 'Канал не найден или недоступен в вашей области доступа.' : error} {errorStatus !== 403 && errorStatus !== 404 && <button onClick={()=>setRetry(x=>x+1)}>Повторить</button>}</p></section>
  if(!channel)return <section className="content-page"><p role="status">Загружаем канал…</p></section>
  return <section className="content-page channel-page">
    <Link className="back-link" to={`/objects/${channel.object_id}`}>← {object?.name??'К объекту'}</Link>
    {virtualTime&&<p className="inline-note">Историческое воспроизведение · Данные на {formatMoscowDateTime(virtualTime)}</p>}
    <header className="content-page-heading"><div><span className="page-eyebrow">Канал датчика · объект {object?.name??channel.object_id}</span><h1>{channel.sensor_type||channel.name||'Канал'}</h1>{channel.name&&channel.name!==channel.sensor_type&&<p>{channel.name}</p>}</div><span className={`state-pill ${channel.latest_alarm===true?'state-critical':channel.latest_value==null?'state-degraded':'state-normal'}`}>{channel.latest_alarm===true?'Тревога':channel.latest_value==null?'Нет актуального значения':'Норма'}</span></header>
    {error&&<p role="alert">{error}</p>}
    <section className="content-panel channel-current-state"><div className="content-panel-heading"><div><span className="page-eyebrow">Последнее зарегистрированное состояние</span><h2>Текущее значение</h2></div></div><strong className="channel-current-value">{channel.latest_value??'Значение временно недоступно'}</strong><p className="object-updated-at">Обновлено: {channel.last_event_at?formatMoscowDateTime(channel.last_event_at):'время неизвестно'}</p></section>
    <section className="content-panel channel-history-panel"><div className="content-panel-heading"><div><span className="page-eyebrow">Зарегистрированные наблюдения</span><h2>История событий</h2></div></div>
      <div className="channel-history-toolbar">
        <label>Период <select value={period} onChange={e=>changePeriod(e.target.value)}><option value="1">24 часа</option><option value="7">7 дней</option><option value="30">30 дней</option><option value="custom">Свой период</option></select></label>
        {period!=='custom'&&<span className="inline-status">До: {formatMoscowDateTime(virtualTime??channel.last_event_at)}</span>}
        <label className="channel-alarm-filter"><input type="checkbox" checked={alarm} onChange={e=>{setAlarm(e.target.checked);setOffset(0);setHistory(null)}}/>Только тревожные</label>
      </div>
      {period==='custom'&&<div className="channel-custom-period"><label>С — МСК <input type="datetime-local" value={from} onChange={e=>{setFrom(e.target.value);setOffset(0);setHistory(null)}}/></label><label>По — МСК <input type="datetime-local" value={to} onChange={e=>{setTo(e.target.value);setOffset(0);setHistory(null)}}/></label></div>}
      {period!=='custom'&&<p className="section-description">Период заканчивается {virtualTime?'временем воспроизведения':'последним зарегистрированным событием'}.</p>}
      {historyError&&<p role="alert">{historyError} <button onClick={()=>setRetry(x=>x+1)}>Повторить</button></p>}
      {!visibleHistory&&!historyError&&<p role="status">{period==='custom'&&(!from||!to)?'Выберите начало и конец периода.':'Загружаем события…'}</p>}
      {visibleHistory&&!historyError&&<><p className="channel-event-count">Событий на странице: {visibleHistory.events.length}</p>{!visibleHistory.events.length&&<p className="page-state page-state-empty">За выбранный период событий нет.</p>}<ol className="event-timeline channel-event-timeline">{visibleHistory.events.map((event,index)=><li className="event-record channel-event-row" key={`${event.timestamp}-${offset}-${index}`}><time>{formatMoscowDateTime(event.timestamp)}</time><div><strong>{event.value??'Значение отсутствует'}</strong><span>{event.alarm===true?'Зафиксирована тревога':'Зарегистрированное состояние'}</span></div>{event.alarm===true&&<span className="state-pill state-critical">Тревога</span>}</li>)}</ol><nav className="pagination" aria-label="Страницы событий"><button disabled={!offset} onClick={()=>{setOffset(x=>Math.max(0,x-50));setHistory(null)}}>Новее</button><span>{offset+1}–{offset+visibleHistory.events.length}</span><button disabled={visibleHistory.events.length<50} onClick={()=>{setOffset(x=>x+50);setHistory(null)}}>Старее</button></nav></>}
    </section>
    <details className="content-panel object-technical-details"><summary>Технические сведения</summary><p>Идентификатор канала: {channel.id}</p><p>Система: {channel.system_type??'Не указана'}</p><p>Тег: {channel.tag??'Не указан'}</p></details>
  </section>
}
