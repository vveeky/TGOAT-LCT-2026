import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { apiFetch } from '../api/client'
import { demoScenarios } from '../scenarios/demoScenarios'

const VARIANTS = [
  { id: 'fire', name: 'Пожарные признаки', sample: 99 },
  { id: 'flooding', name: 'Подтопление', sample: 111 },
  { id: 'gas', name: 'Газ', sample: 28 },
  { id: 'intrusion', name: 'Охрана и доступ', sample: 20 },
  { id: 'anomalous_temperature', name: 'Аномальная температура', sample: 28 },
  { id: 'communication_loss', name: 'Потеря связи', sample: 28 },
  { id: 'power_loss', name: 'Электроснабжение', sample: 28 },
]
const STATES = {
  alarm_observed: 'Зарегистрирована тревога · требуется проверка',
  non_alarm_observed: 'Последнее событие не тревожное',
  stale: 'Данные устарели',
  no_observation_in_window: 'Нет наблюдений в доступном окне',
  unknown_alarm_state: 'Не удалось определить состояние',
}
const CONCLUSIONS = {
  observed_sensor_alarm_requires_verification: 'Есть зарегистрированные тревожные признаки — требуется верификация',
  insufficient_evidence: 'Недостаточно подтверждённых данных для вывода',
  no_recent_sensor_alarm_observed: 'В доступной свежей телеметрии тревога не зарегистрирована',
}
const HISTORY_SOURCES = {
  historical_replay: 'исторический просмотр',
  simulated_live: 'симулируемый поток зарегистрированных событий',
  annual_event_history_latest_only: 'доступна только последняя запись по каждому каналу',
  unavailable: 'источник недоступен',
}
const SEQUENCE_COMPLETENESS = {
  complete_within_selected_replay_window: 'все сработки в окне выбранного повтора',
  bounded_ingested_event_window: 'сработки в пределах загруженного окна потока',
  incomplete_latest_state_per_channel_only: 'неполная: доступны только последние состояния каналов',
  unavailable: 'источник недоступен',
  unknown: 'полнота не установлена',
}
const OBSERVABILITY = {
  loss_indicated: 'Свежие наблюдения отсутствуют',
  currently_observed: 'Все выбранные каналы имеют свежие наблюдения',
  partial_or_unknown: 'Наблюдаемость частичная или не установлена',
}

function dateTime(value) {
  if (!value) return 'Нет данных'
  const result = new Date(value)
  return Number.isNaN(result.getTime())
    ? 'Неизвестное время'
    : `${result.toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' })} МСК`
}

function EvidenceList({ rows = [] }) {
  return (
    <section className="scenario-section-card" aria-labelledby="scenario-evidence-title">
      <header className="scenario-section-heading">
        <div>
          <span className="scenario-section-eyebrow">ТЕЛЕМЕТРИЯ</span>
          <h3 id="scenario-evidence-title">Зарегистрированные признаки</h3>
        </div>
        <span className="scenario-count">{rows.length}</span>
      </header>
      {rows.length === 0 ? (
        <div className="scenario-empty-row">Подходящие датчики отсутствуют в реестре объекта.</div>
      ) : (
        <div className="scenario-evidence-list">
          {rows.map((evidence) => {
            const tone = evidence.state === 'alarm_observed'
              ? 'is-warning'
              : evidence.state === 'non_alarm_observed' ? 'is-clear' : 'is-muted'
            return (
              <article className="scenario-evidence-row" key={evidence.channel_id}>
                <span className={`scenario-evidence-indicator ${tone}`} aria-hidden="true" />
                <div className="scenario-evidence-content">
                  <div className="scenario-evidence-heading">
                    <div>
                      <Link to={`/channels/${evidence.channel_id}`}>
                        {evidence.sensor_name || evidence.sensor_type}
                      </Link>
                      <p>{evidence.sensor_type} · канал {evidence.channel_id}</p>
                    </div>
                    <span className={`scenario-evidence-state ${tone}`}>
                      {STATES[evidence.state] || 'Состояние не определено'}
                    </span>
                  </div>
                  <div className="scenario-evidence-meta">
                    <span><small>Последняя запись</small><time>{dateTime(evidence.latest_at)}</time></span>
                    {evidence.latest_value != null && (
                      <span><small>Значение источника</small><strong>{String(evidence.latest_value)}</strong></span>
                    )}
                    <span><small>Выборка событий</small><strong>{evidence.sampled_observations} · тревожных {evidence.sampled_alarms}</strong></span>
                  </div>
                  {evidence.source && <small className="scenario-evidence-source">Источник: {evidence.source}</small>}
                </div>
              </article>
            )
          })}
        </div>
      )}
    </section>
  )
}

function ScenarioLabPage() {
  const [params, setParams] = useSearchParams()
  const specified = VARIANTS.some((item) => item.id === params.get('type'))
  const scenarioType = specified ? params.get('type') : 'fire'
  const current = VARIANTS.find((variant) => variant.id === scenarioType)
  const objectFromQuery = Number(params.get('object'))
  const objectId = Number.isSafeInteger(objectFromQuery) && objectFromQuery > 0 ? objectFromQuery : current.sample
  const [edited, setEdited] = useState({ objectId, value: String(objectId) })
  const editedId = edited.objectId === objectId ? edited.value : String(objectId)
  const requestKey = `${scenarioType}:${objectId}`
  const [response, setResponse] = useState({ key: null, result: null, error: null })
  const state = response.key === requestKey ? response : { loading: true, result: null, error: null }

  useEffect(() => {
    const abort = new AbortController()
    apiFetch(`/scenarios/${scenarioType}/objects/${objectId}`, { signal: abort.signal })
      .then((result) => { if (!abort.signal.aborted) setResponse({ key: requestKey, result, error: null }) })
      .catch((error) => { if (!abort.signal.aborted) setResponse({ key: requestKey, result: null, error: error.message }) })
    return () => abort.abort()
  }, [scenarioType, objectId, requestKey])

  function choose(type, object = VARIANTS.find((item) => item.id === type).sample) {
    setEdited({ objectId: object, value: String(object) })
    setParams({ type, object: String(object) })
  }

  const result = state.result
  const freshChannels = result?.observability?.fresh_channel_count ?? 0
  const relevantChannels = result?.observability?.relevant_channel_count ?? 0
  const freshnessPercent = relevantChannels > 0
    ? Math.min(100, (freshChannels / relevantChannels) * 100)
    : 0
  const hasObservedAlarm = result?.classification === 'observed_sensor_alarm_requires_verification'
  const resultTone = hasObservedAlarm
    ? 'is-warning'
    : result?.classification === 'no_recent_sensor_alarm_observed' ? 'is-neutral' : 'is-muted'
  const resultStatus = hasObservedAlarm
    ? 'Требуется верификация'
    : result?.classification === 'insufficient_evidence'
      ? 'Недостаточно данных'
      : result?.classification === 'no_recent_sensor_alarm_observed'
        ? 'Тревога не зарегистрирована'
        : 'Состояние не определено'

  return (
    <section className="content-page scenario-lab-page">
      <header className="content-page-heading scenario-page-heading">
        <div>
          <span className="page-eyebrow">АНАЛИЗ СИТУАЦИЙ</span>
          <h1>Сценарии</h1>
          <p>Сопоставление телеметрии, плановых работ и контекста объекта для проверки диспетчером.</p>
        </div>
      </header>

      <details className="scenario-guidance">
        <summary>
          <span className="scenario-guidance-mark" aria-hidden="true">i</span>
          <span className="scenario-guidance-copy">
            <strong>Как читать результат</strong>
            <small>Сработка датчика требует проверки и не подтверждает аварию.</small>
          </span>
          <span className="scenario-guidance-chevron" aria-hidden="true" />
        </summary>
        <div className="scenario-guidance-body">
          <p>Тревожное сообщение означает срабатывание источника и требует проверки диспетчером или дежурным инженером. Оно не подтверждает аварию и само по себе не означает отправку ремонтной бригады.</p>
          <p>Оценка ML — общий риск тревожного события в следующие 24 часа, а не вероятность конкретного физического инцидента. Решение всегда принимает диспетчер.</p>
        </div>
      </details>

      <section className="scenario-setup-panel" aria-labelledby="scenario-setup-title">
        <div className="scenario-setup-heading">
          <div>
            <span className="scenario-section-eyebrow">НАСТРОЙКА АНАЛИЗА</span>
            <h2 id="scenario-setup-title">Выберите сценарий и объект</h2>
          </div>
          <span className="scenario-step-label">Шаг 1 из 2</span>
        </div>

        <div className="scenario-variant-grid" role="group" aria-label="Тип сценария">
          {VARIANTS.map((item, index) => (
            <button
              type="button"
              className="scenario-variant"
              key={item.id}
              onClick={() => choose(item.id, objectId)}
              aria-label={item.name}
              aria-pressed={item.id === scenarioType}
            >
              <span className="scenario-variant-number">{String(index + 1).padStart(2, '0')}</span>
              <strong>{item.name}</strong>
              <span className="scenario-variant-state">{item.id === scenarioType ? 'Выбран' : 'Выбрать'}</span>
            </button>
          ))}
        </div>

        <form className="scenario-object-form" onSubmit={(event) => {
          event.preventDefault()
          const next = Number(editedId)
          if (Number.isSafeInteger(next) && next > 0) choose(scenarioType, next)
        }}>
          <label htmlFor="scenario-object-id">Идентификатор объекта</label>
          <div className="scenario-object-controls">
            <div className="scenario-object-input-wrap">
              <span aria-hidden="true">№</span>
              <input
                id="scenario-object-id"
                inputMode="numeric"
                value={editedId}
                onChange={(event) => setEdited({ objectId, value: event.target.value })}
                required
                pattern="[1-9][0-9]*"
              />
            </div>
            <button type="submit" className="scenario-run-button">
              Показать анализ <span aria-hidden="true">→</span>
            </button>
          </div>
        </form>

        <details className="scenario-examples">
          <summary>
            <span>Быстрые примеры из реестра</span>
            <span className="scenario-examples-count">{demoScenarios.length}</span>
          </summary>
          <div className="scenario-example-list">
            {demoScenarios.map((item) => (
              <button
                type="button"
                className="scenario-example"
                key={item.id}
                onClick={() => choose(
                  item.id === 'smoke-fire' ? 'fire' : item.id === 'access-verification' ? 'intrusion' : 'flooding',
                  item.objectId,
                )}
              >
                <span><strong>{item.title}</strong><small>Объект {item.objectId}</small></span>
                <span aria-hidden="true">→</span>
              </button>
            ))}
          </div>
        </details>
      </section>

      {state.loading && <div className="scenario-feedback" role="status"><span className="scenario-feedback-spinner" aria-hidden="true" />Загружаем зарегистрированные данные…</div>}
      {state.error && <div className="scenario-feedback is-error" role="alert"><strong>Не удалось получить анализ</strong><span>{state.error}. Проверьте права, доступность источника и ID объекта.</span></div>}

      {result && (
        <section className="scenario-results" aria-labelledby="scenario-result-title" aria-live="polite">
          <header className="scenario-result-heading">
            <div>
              <span className="scenario-section-eyebrow">РЕЗУЛЬТАТ ПРОВЕРКИ</span>
              <h2 id="scenario-result-title">
                <span className="sr-only">{result.title} · объект {result.object_id}</span>
                <span aria-hidden="true">{result.title}</span>
                <span aria-hidden="true">· объект {result.object_id}</span>
              </h2>
            </div>
            <span className={`scenario-result-status ${resultTone}`}>
              <span aria-hidden="true" />
              {resultStatus}
            </span>
          </header>

          <div className="scenario-result-overview">
            <article className="scenario-observability-card">
              <div className="scenario-overview-card-heading">
                <span>Наблюдаемость каналов</span>
                <span className="scenario-card-index">01</span>
              </div>
              <div className="scenario-freshness-value">
                <strong>{freshChannels}</strong><span>/ {relevantChannels} свежих каналов</span>
              </div>
              <div
                className="scenario-freshness-track"
                role="progressbar"
                aria-label="Доля каналов со свежими наблюдениями"
                aria-valuemin="0"
                aria-valuemax="100"
                aria-valuenow={Math.round(freshnessPercent)}
              >
                <span style={{ width: `${freshnessPercent}%` }} />
              </div>
              <p className="scenario-observability-state">{OBSERVABILITY[result.observability?.status] || 'Состояние не установлено'}</p>
              <p className="scenario-observability-note">Отсутствие связи или свежих наблюдений не означает отсутствия инцидента.</p>
            </article>

            <article className="scenario-risk-card">
              <div className="scenario-overview-card-heading">
                <span>Общий ML-риск · 24 часа</span>
                <span className="scenario-card-index">02</span>
              </div>
              {result.generic_proxy_risk ? (
                <>
                  <div className="scenario-risk-value">{Math.round(100 * result.generic_proxy_risk.probability)}<span>%</span></div>
                  <p>Общий риск тревожного события. Это не вероятность выбранного сценария.</p>
                  <small>Снимок модели · {dateTime(result.generic_proxy_risk.snapshot_ts)}</small>
                </>
              ) : (
                <div className="scenario-risk-unavailable">Актуальная общая ML-оценка для выбранного момента недоступна.</div>
              )}
            </article>
          </div>

          <div className="scenario-result-meta">
            <span><small>Вывод</small><strong>{CONCLUSIONS[result.classification] || result.classification}</strong></span>
            <span><small>Время оценки</small><time>{dateTime(result.as_of)}</time></span>
            <span><small>Источник событий</small><strong>{HISTORY_SOURCES[result.history_source] || 'не установлен'}</strong></span>
          </div>

          {scenarioType === 'power_loss' && (
            <p className="scenario-caution">Сработка контроля фазы или тревожного канала не подтверждает полную потерю электроснабжения.</p>
          )}

          <div className="scenario-result-columns">
            <div className="scenario-result-main">
              <EvidenceList rows={result.evidence ?? []} />

              <section className="scenario-section-card" aria-labelledby="scenario-recommendations-title">
                <header className="scenario-section-heading">
                  <div>
                    <span className="scenario-section-eyebrow">СЛЕДУЮЩИЕ ДЕЙСТВИЯ</span>
                    <h3 id="scenario-recommendations-title">Рекомендации для верификации</h3>
                  </div>
                  <span className="scenario-count">{result.recommendations.length}</span>
                </header>
                {result.recommendations.length ? (
                  <ol className="scenario-recommendation-list">
                    {result.recommendations.map((recommendation, index) => (
                      <li key={recommendation.code}>
                        <span className="scenario-recommendation-number">{String(index + 1).padStart(2, '0')}</span>
                        <span>
                          <strong>{recommendation.title}</strong>
                          <small>{recommendation.detail}</small>
                          {recommendation.basis_channels.length > 0 && <small>Основание · каналы {recommendation.basis_channels.join(', ')}</small>}
                        </span>
                      </li>
                    ))}
                  </ol>
                ) : <p className="scenario-empty-row">Рекомендаций для доступных данных нет.</p>}
              </section>

              {scenarioType === 'intrusion' && result.intrusion_route && (
                <section className="scenario-section-card" aria-labelledby="scenario-route-title">
                  <header className="scenario-section-heading">
                    <div>
                      <span className="scenario-section-eyebrow">ОХРАНА И ДОСТУП</span>
                      <h3 id="scenario-route-title">Последовательность сработок охранных датчиков</h3>
                    </div>
                    <span className="scenario-count">{result.intrusion_route.points.length}</span>
                  </header>
                  <p className="scenario-section-note">{result.intrusion_route.limitation} Полнота выборки: {SEQUENCE_COMPLETENESS[result.intrusion_route.sequence_completeness] || 'не установлена'}.</p>
                  {result.intrusion_route.points.length ? (
                    <ol className="scenario-timeline">
                      {result.intrusion_route.points.map((point, index) => (
                        <li key={`${point.channel_id}-${point.timestamp}-${index}`}>
                          <span className="scenario-timeline-index">{String(index + 1).padStart(2, '0')}</span>
                          <span><strong>{point.sensor_name || `Канал ${point.channel_id}`}</strong><small>{dateTime(point.timestamp)}{point.picket != null && ` · пикет ${point.picket}`}{point.coordinates && ` · ${point.coordinates.lat}, ${point.coordinates.lon}`}</small></span>
                        </li>
                      ))}
                    </ol>
                  ) : <p className="scenario-empty-row">В доступной выборке сработки не найдены. Это не доказывает, что события не было.</p>}
                </section>
              )}

              {scenarioType === 'flooding' && (
                <section className="scenario-section-card" aria-labelledby="scenario-weather-title">
                  <header className="scenario-section-heading">
                    <div>
                      <span className="scenario-section-eyebrow">КОНТЕКСТ</span>
                      <h3 id="scenario-weather-title">Метеоданные</h3>
                    </div>
                    <span className="scenario-count">Город</span>
                  </header>
                  <p className="scenario-section-note">Общегородские сведения, не показания датчика объекта. Для исторического просмотра учитываются только прогнозы, выпущенные к выбранному моменту.</p>
                  {result.weather?.status === 'available' ? (
                    <>
                      <p className="scenario-weather-scope">Область: {result.weather.geographic_scope}</p>
                      <div className="scenario-weather-list">
                        {result.weather.forecasts?.slice(0, 8).map((item, index) => (
                          <article key={`${item.issued_at}-${item.valid_at}-${index}`}>
                            <strong>Прогноз · {dateTime(item.valid_at)}</strong>
                            <span>Получен {dateTime(item.issued_at)}</span>
                            <small>Осадки {item.precipitation_mm ?? '—'} мм · температура {item.temperature_c ?? '—'} °C · влажность {item.relative_humidity_percent ?? '—'}% · давление {item.pressure_hpa ?? '—'} гПа</small>
                          </article>
                        ))}
                        {result.weather.observations?.slice(0, 4).map((item, index) => (
                          <article key={`${item.valid_at}-${index}`}>
                            <strong>Зарегистрированная погода · {dateTime(item.valid_at)}</strong>
                            <small>Осадки {item.precipitation_mm ?? '—'} мм · температура {item.temperature_c ?? '—'} °C · влажность {item.relative_humidity_percent ?? '—'}% · давление {item.pressure_hpa ?? '—'} гПа</small>
                          </article>
                        ))}
                      </div>
                    </>
                  ) : <p className="scenario-empty-row">Метеоисточник недоступен — погодные значения не предполагаются и не подставляются.</p>}
                </section>
              )}
            </div>

            <aside className="scenario-result-aside">
              <section className="scenario-section-card" aria-labelledby="scenario-missing-title">
                <header className="scenario-section-heading">
                  <div>
                    <span className="scenario-section-eyebrow">КАЧЕСТВО ДАННЫХ</span>
                    <h3 id="scenario-missing-title">Ограничения анализа</h3>
                  </div>
                </header>
                {result.missing_evidence.length ? (
                  <ul className="scenario-limitation-list">{result.missing_evidence.map((line) => <li key={line}>{line}</li>)}</ul>
                ) : <p className="scenario-empty-row">Дополнительные пробелы в доступной выборке не обнаружены. Это не подтверждает отсутствие инцидента.</p>}
              </section>

              <section className="scenario-section-card" aria-labelledby="scenario-work-title">
                <header className="scenario-section-heading">
                  <div>
                    <span className="scenario-section-eyebrow">РТЭКК</span>
                    <h3 id="scenario-work-title">Плановые работы</h3>
                  </div>
                </header>
                <div className="scenario-work-counts">
                  <span><strong>{result.planned_work.active.length}</strong><small>Активных</small></span>
                  <span><strong>{result.planned_work.recent.length}</strong><small>Недавних</small></span>
                  <span><strong>{result.planned_work.unmapped_records}</strong><small>Без привязки</small></span>
                </div>
                <p className="scenario-section-note">Источник сведений о работах и составе инженерных систем. Серия тревог не считается ППР без подтверждённой привязки к записи работ.</p>
                {result.planned_work.unmapped_records > 0 && <p className="scenario-work-warning">Отсутствие работ по объекту подтвердить нельзя.</p>}
                {result.planned_work.active.length > 0 && (
                  <ul className="scenario-work-list">
                    {result.planned_work.active.map((work) => (
                      <li key={work.planned_work_id}>
                        <strong>{work.work_type}</strong>
                        <span>{work.date_precision === 'month_only' ? work.planned_start : work.planned_start?.slice(0, 10)}</span>
                        <small>{work.source_file}</small>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <details className="scenario-glossary">
                <summary>Справочник оборудования и ограничений</summary>
                <dl>{Object.entries(result.equipment_terminology?.abbreviations || {}).map(([shortName, meaning]) => (
                  <div key={shortName}><dt><strong>{shortName}</strong></dt><dd>{meaning}</dd></div>
                ))}</dl>
                <ul>{(result.equipment_terminology?.designation_rules || []).map((rule) => <li key={rule}>{rule}</li>)}</ul>
                <p>Шаг пикета обычно 10 м, но меняется. Возможные причины неисправности фидера — диагностические варианты, перечень не исчерпывающий; сообщение «Неисправен» само причину не устанавливает.</p>
              </details>
            </aside>
          </div>

          <nav className="scenario-result-actions" aria-label="Дальнейшие действия">
            <Link className="scenario-result-primary" to={result.decision_route}>Перейти к объекту или сигналу <span aria-hidden="true">→</span></Link>
            <Link className="scenario-result-secondary" to="/signals">Журнал ситуаций</Link>
          </nav>
        </section>
      )}
    </section>
  )
}

export default ScenarioLabPage
