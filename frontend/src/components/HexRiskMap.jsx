import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  HexGrid,
  Layout,
  Hexagon,
} from 'react-hexgrid'


/* =====================================================
   MOCK DATA
===================================================== */

const riskSequence = [
  ...Array(791).fill('normal'),
  ...Array(131).fill('medium'),
  ...Array(70).fill('high'),
  ...Array(20).fill('critical'),
]

const scoreRanges = {
  normal: [0.05, 0.34],
  medium: [0.35, 0.59],
  high: [0.60, 0.84],
  critical: [0.85, 0.99],
}


function seededRandom(seed) {
  let value = seed

  return function () {
    value = (value * 9301 + 49297) % 233280
    return value / 233280
  }
}


function shuffleArray(array) {
  const result = [...array]
  const random = seededRandom(1337)

  for (let i = result.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1))

    ;[result[i], result[j]] = [result[j], result[i]]
  }

  return result
}


function getScore(risk, index) {
  const [min, max] = scoreRanges[risk]

  const value =
    ((index * 37 + 17) % 100) / 100

  return min + (max - min) * value
}


const shuffledRisks = shuffleArray(riskSequence)


const objects = shuffledRisks.map((risk, index) => ({
  id: index + 1,

  name:
    index === 19
      ? 'Объект Фита'
      : `Объект ${String(index + 1).padStart(3, '0')}`,

  type:
    index % 3 === 0
      ? 'guardObject'
      : 'engineeringObject',

  risk,

  score: getScore(risk, index),

  channels:
    40 + ((index * 17) % 150),

  dangerousChannels:
    risk === 'normal'
      ? 0
      : 1 + ((index * 7) % 8),

  predictions:
    risk === 'normal'
      ? 0
      : 1 + ((index * 5) % 4),

  requests:
    risk === 'critical'
      ? 2
      : risk === 'high'
        ? 1
        : 0,

  forecast:
    risk === 'critical'
      ? '0–24 часа'
      : risk === 'high'
        ? '24–48 часов'
        : risk === 'medium'
          ? '48–72 часа'
          : 'Активного прогноза нет',

  factors:
    risk === 'normal'
      ? []
      : [
          'Рост частоты тревог',
          'Изменение интервалов между событиями',
          'Отклонение от базового профиля',
        ],
}))


function getRiskLabel(risk) {
  const labels = {
    normal: 'Норма',
    medium: 'Средний',
    high: 'Высокий',
    critical: 'Критический',
  }

  return labels[risk]
}


/* =====================================================
   HEX POSITION
===================================================== */

const COLUMNS = 46


function getHexCoordinates(index) {
  const row = Math.floor(index / COLUMNS)
  const col = index % COLUMNS

  const q = col
  const r = row - Math.floor(col / 2)
  const s = -q - r

  return {
    q,
    r,
    s,
  }
}


/* =====================================================
   COMPONENT
===================================================== */

function HexRiskMap() {
  const navigate = useNavigate()

  const [selectedObject, setSelectedObject] =
    useState(null)

  const [hoveredObject, setHoveredObject] =
    useState(null)

  const [hoveredIndex, setHoveredIndex] =
    useState(null)


  const criticalCount = objects.filter(
    (object) => object.risk === 'critical'
  ).length

  const highCount = objects.filter(
    (object) => object.risk === 'high'
  ).length

  const mediumCount = objects.filter(
    (object) => object.risk === 'medium'
  ).length

  const normalCount = objects.filter(
    (object) => object.risk === 'normal'
  ).length


  const topObjects = [...objects]
    .filter((object) => object.risk !== 'normal')
    .sort((a, b) => b.score - a.score)
    .slice(0, 5)


  const hoveredCoordinates =
    hoveredIndex !== null
      ? getHexCoordinates(hoveredIndex)
      : null


  return (
    <div className="host-workspace">

      {/* =================================================
          HEX MAP
      ================================================= */}

      <section className="host-map-area">

        <div className="host-grid-wrapper">

          <HexGrid
            width="100%"
            height="100%"
            viewBox="-74 -42 148 84"
          >

            <Layout
              size={{ x: 1.75, y: 1.75 }}
              flat={true}
              spacing={1.10}
              origin={{ x: -68, y: -38 }}
            >

              {/* =========================================
                  MAIN HEXES
              ========================================= */}

              {objects.map((object, index) => {
                const {
                  q,
                  r,
                  s,
                } = getHexCoordinates(index)

                const isSelected =
                  selectedObject?.id === object.id


                return (
                  <Hexagon
                    key={object.id}

                    q={q}
                    r={r}
                    s={s}

                    className={
                      `risk-hex ${object.risk}` +
                      (isSelected
                        ? ' selected'
                        : '')
                    }

                    onClick={() =>
                      setSelectedObject(object)
                    }

                    onMouseEnter={() => {
                      setHoveredObject(object)
                      setHoveredIndex(index)
                    }}

                    onMouseLeave={() => {
                      setHoveredObject(null)
                      setHoveredIndex(null)
                    }}
                  />
                )
              })}


              {/* =========================================
                  HOVER COPY

                  Рисуется последней,
                  поэтому всегда поверх соседних сот.
              ========================================= */}

              {hoveredObject &&
                hoveredCoordinates && (
                  <Hexagon
                    q={hoveredCoordinates.q}
                    r={hoveredCoordinates.r}
                    s={hoveredCoordinates.s}

                    className={
                      `risk-hex hover-hex ${hoveredObject.risk}`
                    }

                    pointerEvents="none"
                  />
                )}

            </Layout>

          </HexGrid>


          {/* =============================================
              TOOLTIP
          ============================================= */}

          {hoveredObject && (
            <div className="host-tooltip">

              <div className="host-tooltip-name">
                {hoveredObject.name}
              </div>

              <div className="host-tooltip-meta">
                {getRiskLabel(
                  hoveredObject.risk
                )}
              </div>

              <strong>
                {Math.round(
                  hoveredObject.score * 100
                )}
                %
              </strong>

            </div>
          )}


          {/* =============================================
              LEGEND
          ============================================= */}

          <div className="host-map-overlay-legend">

            <span>
              <i className="legend-dot normal" />
              Норма
            </span>

            <span>
              <i className="legend-dot medium" />
              Средний
            </span>

            <span>
              <i className="legend-dot high" />
              Высокий
            </span>

            <span>
              <i className="legend-dot critical" />
              Критический
            </span>

          </div>

        </div>

      </section>


      {/* =================================================
          RIGHT INFO
      ================================================= */}

        <aside className="host-info-panel">

        <div className="dashboard-side-header">
            <h1>Дашборд</h1>

            <p>
            Общее состояние контролируемой инфраструктуры
            </p>
        </div>

        {!selectedObject ? (

          <>
            <div className="info-panel-header">
              <h3>
                Общее состояние
              </h3>

              <p>
                Контролируемая инфраструктура
              </p>
            </div>


            <div className="system-overview-main">

              <div>
                <span>
                  Объектов
                </span>

                <strong>
                  {objects.length}
                </strong>
              </div>


              <div className="system-overview-risk">
                <span>
                  Требуют внимания
                </span>

                <strong>
                  {mediumCount +
                    highCount +
                    criticalCount}
                </strong>
              </div>

            </div>


            {/* KPI */}

            <div className="system-kpi-list">

              <div className="system-kpi-row">
                <div>
                  <i className="kpi-dot critical" />

                  <span>
                    Критический риск
                  </span>
                </div>

                <strong>
                  {criticalCount}
                </strong>
              </div>


              <div className="system-kpi-row">
                <div>
                  <i className="kpi-dot high" />

                  <span>
                    Высокий риск
                  </span>
                </div>

                <strong>
                  {highCount}
                </strong>
              </div>


              <div className="system-kpi-row">
                <div>
                  <i className="kpi-dot medium" />

                  <span>
                    Средний риск
                  </span>
                </div>

                <strong>
                  {mediumCount}
                </strong>
              </div>


              <div className="system-kpi-row">
                <div>
                  <i className="kpi-dot request" />

                  <span>
                    Активные заявки
                  </span>
                </div>

                <strong>
                  9
                </strong>
              </div>

            </div>


            {/* DISTRIBUTION */}

            <div className="info-section">

              <div className="info-section-title">
                Распределение рисков
              </div>


              <div className="risk-stat-row">

                <span>
                  Норма
                </span>

                <div className="risk-stat-track">
                  <div
                    className="risk-stat-fill normal"
                    style={{
                      width:
                        `${(normalCount / objects.length) * 100}%`,
                    }}
                  />
                </div>

                <strong>
                  {normalCount}
                </strong>

              </div>


              <div className="risk-stat-row">

                <span>
                  Средний
                </span>

                <div className="risk-stat-track">
                  <div
                    className="risk-stat-fill medium"
                    style={{
                      width:
                        `${(mediumCount / objects.length) * 100}%`,
                    }}
                  />
                </div>

                <strong>
                  {mediumCount}
                </strong>

              </div>


              <div className="risk-stat-row">

                <span>
                  Высокий
                </span>

                <div className="risk-stat-track">
                  <div
                    className="risk-stat-fill high"
                    style={{
                      width:
                        `${(highCount / objects.length) * 100}%`,
                    }}
                  />
                </div>

                <strong>
                  {highCount}
                </strong>

              </div>


              <div className="risk-stat-row">

                <span>
                  Критический
                </span>

                <div className="risk-stat-track">
                  <div
                    className="risk-stat-fill critical"
                    style={{
                      width:
                        `${(criticalCount / objects.length) * 100}%`,
                    }}
                  />
                </div>

                <strong>
                  {criticalCount}
                </strong>

              </div>

            </div>


            {/* ATTENTION */}

            <div className="info-section">

              <div className="info-section-title">
                Требуют внимания
              </div>


              <div className="attention-list">

                {topObjects.map((object) => (
                  <button
                    key={object.id}

                    type="button"

                    className="attention-item"

                    onClick={() =>
                      setSelectedObject(object)
                    }
                  >

                    <div className="attention-item-info">
                      <strong>
                        {object.name}
                      </strong>

                      <span>
                        {
                          object.dangerousChannels
                        } проблемных каналов
                      </span>
                    </div>


                    <div className="attention-score">
                      {Math.round(
                        object.score * 100
                      )}
                      %
                    </div>

                  </button>
                ))}

              </div>

            </div>


            {/* PREDICTIONS */}

            <div className="info-section">

              <div className="info-section-title">
                Последние прогнозы
              </div>


              <div className="prediction-mini">

                <div className="prediction-mini-item">
                  <span className="prediction-time">
                    14:42
                  </span>

                  <div>
                    <strong>
                      Объект 148
                    </strong>

                    <small>
                      HIGH · 82% · 24–48 ч
                    </small>
                  </div>
                </div>


                <div className="prediction-mini-item">
                  <span className="prediction-time">
                    14:37
                  </span>

                  <div>
                    <strong>
                      Объект 317
                    </strong>

                    <small>
                      CRITICAL · 94% · 0–24 ч
                    </small>
                  </div>
                </div>


                <div className="prediction-mini-item">
                  <span className="prediction-time">
                    14:31
                  </span>

                  <div>
                    <strong>
                      Объект 021
                    </strong>

                    <small>
                      HIGH · 78% · 24–48 ч
                    </small>
                  </div>
                </div>

              </div>

            </div>

          </>

        ) : (

          /* =============================================
             SELECTED OBJECT
          ============================================= */

          <>
            <div className="selected-header">

              <button
                type="button"
                className="panel-back"

                onClick={() =>
                  setSelectedObject(null)
                }
              >
                ←
              </button>


              <div>
                <h3>
                  {selectedObject.name}
                </h3>

                <p>
                  ID {selectedObject.id}
                </p>
              </div>

            </div>


            <div className="selected-risk">

              <div className="selected-risk-top">

                <span
                  className={
                    `risk-badge ${selectedObject.risk}`
                  }
                >
                  {getRiskLabel(
                    selectedObject.risk
                  )}
                </span>

                <span className="object-type">
                  {selectedObject.type}
                </span>

              </div>


              <strong>
                {Math.round(
                  selectedObject.score * 100
                )}
                %
              </strong>

              <small>
                Risk score
              </small>

            </div>


            <div className="selected-stat-list">

              <div>
                <span>
                  Каналов
                </span>

                <strong>
                  {selectedObject.channels}
                </strong>
              </div>


              <div>
                <span>
                  High / Critical
                </span>

                <strong>
                  {
                    selectedObject.dangerousChannels
                  }
                </strong>
              </div>


              <div>
                <span>
                  Активные прогнозы
                </span>

                <strong>
                  {selectedObject.predictions}
                </strong>
              </div>


              <div>
                <span>
                  Активные заявки
                </span>

                <strong>
                  {selectedObject.requests}
                </strong>
              </div>

            </div>


            <div className="info-section">

              <div className="info-section-title">
                Ближайший прогноз
              </div>


              <div className="forecast-row">

                <span>
                  Горизонт
                </span>

                <strong>
                  {selectedObject.forecast}
                </strong>

              </div>

            </div>


            {selectedObject.factors.length > 0 && (
              <div className="info-section">

                <div className="info-section-title">
                  Факторы риска
                </div>


                <div className="factor-list">

                  {selectedObject.factors.map(
                    (factor) => (
                      <div key={factor}>
                        {factor}
                      </div>
                    )
                  )}

                </div>

              </div>
            )}


            <div className="info-section">

              <div className="info-section-title">
                Последняя активность
              </div>


              <div className="last-event-row">

                <span>
                  Последнее событие
                </span>

                <strong>
                  Сегодня, 13:20
                </strong>

              </div>

            </div>


            <div className="selected-actions">

              <button
                type="button"

                className="primary-action"

                onClick={() =>
                  navigate(
                    `/objects/${selectedObject.id}`
                  )
                }
              >
                Открыть объект
                <span>→</span>
              </button>


              <button
                type="button"
                className="secondary-action"
              >
                Создать заявку
              </button>

            </div>

          </>
        )}

      </aside>

    </div>
  )
}


export default HexRiskMap