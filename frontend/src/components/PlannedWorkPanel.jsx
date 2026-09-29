import { useEffect, useState } from 'react'
import { getObjectPlannedWork } from '../api/objects'
import { formatMoscowDateTime } from '../dateTime'

function formatMonth(value) {
  const match = /^(\d{4})-(\d{2})$/.exec(value ?? '')
  if (!match) return value
  const [, year, month] = match
  const label = new Intl.DateTimeFormat('ru-RU', {
    month: 'long', year: 'numeric', timeZone: 'UTC',
  }).format(new Date(Date.UTC(Number(year), Number(month) - 1, 1)))
    .replace(/\sг\.$/, '')
  return label.charAt(0).toLocaleUpperCase('ru-RU') + label.slice(1)
}

function workDate(item) {
  if (item.temporal_constraint_kind === 'deadline' && item.constraint_at) {
    const deadline = new Date(item.constraint_at)
    const time = new Intl.DateTimeFormat('ru-RU', {
      hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow',
    }).format(deadline)
    const date = new Intl.DateTimeFormat('ru-RU', {
      day: 'numeric', month: 'long', year: 'numeric', timeZone: 'Europe/Moscow',
    }).format(deadline)
    return item.work_type?.toLocaleLowerCase('ru-RU').includes('передач')
      ? `Передать до ${time} · ${date}`
      : `Срок до ${time} · ${date}`
  }
  if (item.date_precision === 'month_only') return formatMonth(item.planned_start)
  if (item.date_precision === 'date_only') return `Дата: ${item.planned_start} · время в источнике не указано`
  return `${formatMoscowDateTime(item.planned_start)} — ${formatMoscowDateTime(item.planned_end)}`
}

function hasNoMappedWork(data) {
  return ['active', 'upcoming', 'recent'].every(key => !data[key]?.length)
}

export default function PlannedWorkPanel({ objectId, asOf = null }) {
  const objectKey = String(objectId)
  const [result, setResult] = useState({ key: null, data: null, error: false })
  useEffect(() => {
    let active = true
    getObjectPlannedWork(objectId, asOf)
      .then(data => { if (active) setResult({ key: objectKey, data, error: false }) })
      .catch(() => {
        if (active) setResult(previous => ({
          key: objectKey,
          data: previous.key === objectKey ? previous.data : null,
          error: true,
        }))
      })
    return () => { active = false }
  }, [objectId, asOf, objectKey])

  const current = result.key === objectKey ? result : { data: null, error: false }
  const loading = current.data == null && !current.error
  return <section className="content-panel planned-work-panel" aria-labelledby="planned-work-title">
    <div className="content-panel-heading"><h2 id="planned-work-title">Плановые работы</h2></div>
    {loading && <p role="status">Загружаем контекст плановых работ…</p>}
    {current.error && <p role="alert">Не удалось загрузить сведения о плановых работах.</p>}
    {current.data?.source_status === 'not_provided' && <p className="inline-note">Источник графиков ППР/ТО заказчиком не предоставлен; проверить совпадения по времени нельзя.</p>}
    {current.data?.unmapped_records > 0 && hasNoMappedWork(current.data) && <p className="inline-note">Нет плановых работ, однозначно привязанных к этому объекту. В источнике остаются {current.data.unmapped_records} непривязанных записей, поэтому отсутствие работ подтвердить нельзя.</p>}
    {current.data?.unmapped_records > 0 && !hasNoMappedWork(current.data) && <p className="inline-note">В источнике остаются записи плановых работ без однозначной привязки к объекту: {current.data.unmapped_records}.</p>}
    {current.data?.source_status === 'available' && current.data.unmapped_records === 0 && hasNoMappedWork(current.data) && <p>В подключённом источнике нет работ в выбранном временном диапазоне.</p>}
    {current.data && ['active', 'upcoming', 'recent'].map((key) => current.data[key].length > 0 && <div key={key}>
      <h3>{key === 'active' ? 'Сейчас' : key === 'upcoming' ? 'Предстоящие' : 'Недавно завершённые'}</h3>
      <ul>{current.data[key].map(item => <li key={item.planned_work_id}>
        <strong>{item.work_type}</strong>{item.equipment_context && ` · ${item.equipment_context}`}<br />
        {workDate(item)}{item.source_file && <><br />Источник: {item.source_file}</>}
        {item.notes && <><br />{item.notes}</>}
      </li>)}</ul>
    </div>)}
    {current.data && <p className="inline-note">Совпадение по времени не доказывает причинную связь с событием.</p>}
  </section>
}
