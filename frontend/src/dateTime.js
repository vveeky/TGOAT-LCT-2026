const MOSCOW_TIME_ZONE = 'Europe/Moscow'

// datetime-local is a wall time, not the browser's local time zone.
export function moscowWallTimeToUTC(value) {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(value)) throw new RangeError('Invalid Moscow datetime')
  const date = new Date(`${value}+03:00`)
  if (Number.isNaN(date.getTime())) throw new RangeError('Invalid Moscow datetime')
  return date.toISOString()
}

export function formatMoscowDateTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Время недоступно'

  const formatted = new Intl.DateTimeFormat('ru-RU', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: MOSCOW_TIME_ZONE,
  }).format(date)
  return `${formatted} МСК`
}

export function formatMoscowTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Время недоступно'

  const formatted = new Intl.DateTimeFormat('ru-RU', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: MOSCOW_TIME_ZONE,
  }).format(date)
  return `${formatted} МСК`
}
