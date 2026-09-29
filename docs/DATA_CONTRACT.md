# Data contract

## Operational identifiers

Business identity is preserved across forecast, human workflow, and maintenance execution. Prediction identity uses object, model and exact assessment timestamp; ForecastCard groups one model episode; Signal is the human-work projection for the card; Request persists inspection/maintenance work independently of later risk changes.

## Time

API timestamps are timezone-aware UTC. Historical source timestamps are interpreted under the configured source timezone before serving. Historical/replay/scenario reads are bounded by `as_of`; future telemetry must never be visible to an earlier assessment.

## Telemetry

Registered observations are source facts, not physical-incident labels. Alarm state means the processed telemetry classification only. The frozen model predicts a 24-hour proxy alarm/anomaly target and must not be presented as a validated probability of fire, flooding, intrusion, or physical equipment failure.

## Planned work

When supplied, a normalized planned-work source preserves source precision. Month-only records remain month-only. Source text such as `до 900 DD.MM.YYYY` is represented as a deadline at 09:00 Europe/Moscow, not an exact work event. Unmapped customer records are never treated as proof that an object has no planned work.

## External integration

Customer systems are read-only boundaries. The project includes local/simulated adapters for telemetry, equipment registry, work status, weather, and LDAP-compatible identity. A simulator must never be described as a real customer connection.
