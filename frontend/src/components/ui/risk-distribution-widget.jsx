const formatCount = (value) => new Intl.NumberFormat('ru-RU', {
  maximumFractionDigits: 0,
}).format(value)

const formatPercent = (value) => new Intl.NumberFormat('ru-RU', {
  maximumFractionDigits: 1,
}).format(value)

// DepartmentLoadCard composition populated with the dashboard's risk data:
// https://ui.watermelon.sh/dashboard/medesk-dashboard
export default function RiskDistributionWidget({ data, hasStats, hasRiskDistribution }) {
  const hasDistribution = Boolean(hasStats && hasRiskDistribution)
  const risks = data.map((risk) => {
    const hasValue = hasDistribution
      && risk.value !== null
      && risk.value !== undefined
      && Number.isFinite(Number(risk.value))
      && risk.value >= 0

    return {
      ...risk,
      value: hasValue ? Number(risk.value) : null,
    }
  })

  const totalObjects = risks.reduce(
    (total, risk) => total + (risk.value ?? 0),
    0,
  )
  return (
    <div className="dashboard-risk-distribution">
      <div className="dashboard-risk-distribution-summary">
        <div className="dashboard-risk-distribution-total" data-widget-part="primary">
          <span>Всего объектов</span>
          <strong>{hasDistribution ? formatCount(totalObjects) : '—'}</strong>
        </div>

        <div
          aria-label={!hasDistribution
            ? 'Данные распределения рисков отсутствуют'
            : `Распределение по уровням риска: ${formatCount(totalObjects)} объектов`}
          className="dashboard-risk-segments"
          data-widget-part="chart"
          role="img"
        >
          {!hasDistribution && <span className="dashboard-risk-empty-state">Нет данных</span>}
          {risks.map((risk, index) => {
            if (risk.value === null || risk.value <= 0 || totalObjects <= 0) {
              return null
            }
            const share = (risk.value / totalObjects) * 100

            return (
              <span
                aria-hidden="true"
                className={`dashboard-load-segment${share <= 3 ? ' is-small-share' : ''}`}
                key={risk.key}
                style={{
                  '--department-share': risk.value,
                  '--department-color': risk.fill,
                  animationDelay: `${index * 90}ms`,
                }}
                title={`${risk.label}: ${formatCount(risk.value)} · ${formatPercent((risk.value / totalObjects) * 100)}%`}
              />
            )
          })}
        </div>
      </div>

      <div
        aria-label="Количество объектов по уровню риска"
        className="dashboard-risk-distribution-rows"
        data-widget-part="breakdown"
        role="list"
      >
        {risks.map((risk) => (
          <div className="dashboard-risk-distribution-row" key={risk.key} role="listitem">
            <div className="dashboard-risk-distribution-category">
              <span
                aria-hidden="true"
                className="dashboard-risk-distribution-swatch"
                style={{ backgroundColor: risk.fill }}
              />
              <span className="dashboard-risk-distribution-label">{risk.label}</span>
            </div>
            <span aria-hidden="true" className="dashboard-risk-distribution-leader" />
            <div className="dashboard-risk-distribution-values">
              <span className="dashboard-risk-distribution-count">
                {risk.value === null ? '—' : formatCount(risk.value)}
              </span>
              <span className="dashboard-risk-distribution-percent">
                {risk.value !== null && totalObjects > 0
                  ? `${formatPercent((risk.value / totalObjects) * 100)}%`
                  : '—'}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
