# API contract v0.2

**Статус:** `API CONTRACT FROZEN` для frontend integration

**Дата:** 2026-09-17

**Версия / идентификатор:** `tgoat-api-v0.2.0-2026-09-17`

Контракт фиксирует структуру до утверждения ML target. Он поддерживает прогнозы уровня канала, объекта и инженерной системы. Frontend не должен считать `channel_id` обязательным.

Base path: `/api/v1`. Все timestamps в ответах — timezone-aware UTC ISO 8601. Naive query timestamp интерпретируется как `EVENT_SOURCE_TIMEZONE`; aware input нормализуется в UTC.

## Demo authentication

Все бизнес-endpoints требуют `Authorization: Bearer <access_token>`; `/health`, `/`, OpenAPI и `POST /auth/login` доступны без токена. Login принимает `{ "username": ..., "password": ... }`, возвращает bearer token сроком на 8 часов. `GET /auth/me` возвращает текущую личность, роль и корневые object IDs scope. Demo users и парольные verifier-ы находятся в памяти; пользователи настраиваются через `DEMO_AUTH_USERS_JSON` (JSON array с `username`, `password`, `role`, `scope_roots`), а токены подписываются `DEMO_AUTH_TOKEN_SECRET`. Значения по умолчанию предназначены только для локальной демонстрации, не для production.

Роли `technician`, `district_dispatcher` и `central_dispatcher` используют иерархию объектов из reference data. Technician ограничен указанными object subtrees; district dispatcher — своим subtree; central dispatcher имеет полный доступ. Technician может оставить review и создать локальный maintenance draft; закрытие prediction, запись outcome и изменение maintenance status доступны диспетчерам. Auth provider изолирован интерфейсом `AuthProvider`, поэтому будущая LDAP/AD интеграция заменяет provider без изменения маршрутов.

Локальные demo credentials: `tech-28 / tech-demo` (поддерево объекта 28), `tech-9 / tech-demo-2` (поддерево объекта 9), `dispatch-9 / dispatch-demo` (поддерево объекта 9), `central / central-demo` (полный scope). Не использовать эти credentials вне локальной демонстрации.

## Score

`score_kind` определяет отображение `risk_score`:

| score_kind | Отображение |
|---|---|
| `probability` | вероятность, допустим процент |
| `proxy_probability` | процент только с подписью «proxy-оценка» |
| `anomaly_score` | score аномальности, не вероятность |
| `rule_score` | score правил, не вероятность |
| `mock` | демонстрационный score, не вероятность |

## Сущности

### Object

```json
{"id":28,"name":"объект Вита","type":"guardObject","channels_count":134,
 "risk_score":0.82,"risk_level":"high","risk_prediction_id":1042,"risk_score_kind":"mock",
 "active_predictions":3,"active_requests":1,"source_parent_id":9,
 "parent_id":9,"parent_reference_found":true,"level":3,"location_name":"объект Вита",
 "geometry":null,"geometry_source":"not_provided"}
```

Risk выбирается по категории активных predictions объекта. `geometry_source` принимает `real`, `synthetic_mvp`, `unknown` или `not_provided`. При `geometry=null` источник — `unknown` либо `not_provided`; при наличии geometry источник обязателен как `real` или `synthetic_mvp`. Synthetic geometry живёт в отдельном presentation/demo layer, связанном по реальному `object_id`; она не заменяет и не утверждает spatial topology исходных объектов.

### Channel

```json
{"id":2866,"object_id":28,"sensor_type":"Датчик движения",
 "system_type":"Охранная подсистема","name":"ОД  ПК506","tag":"16-1.1.1.2.",
 "risk_score":0.82,"risk_level":"high","risk_prediction_id":1042,
 "risk_score_kind":"mock","last_event_at":"2026-06-24T10:40:10Z"}
```

### Prediction

```json
{"id":1042,"scope_type":"channel","incident_type":"proxy_event","object_id":28,
 "channel_id":2866,"system_ref":null,"score_kind":"mock","model_version":"mock-v1",
 "risk_score":0.82,"risk_level":"high","forecast_from_hours":24,
 "forecast_to_hours":48,"factors":["Рост частоты тревог"],
 "recommendations":[{"prediction_id":1042,"code":"inspect-channel","title":"Проверить канал",
 "description":"Проверить датчик и линию связи по регламенту.","source":"mock"}],
 "created_at":"2026-09-16T10:00:00Z","status":"active","dispatcher_result":null}
```

`object_id` обязателен всегда. `channel` требует `channel_id`; `engineering_system` требует opaque `system_ref`. `incident_type` — открытая строка. `forecast_from_hours >= 0`, `forecast_to_hours >= forecast_from_hours`; business/default target составляет 24 часа, но API допускает другое обоснованное окно. `risk_score` всегда finite; `probability` и `proxy_probability` ограничены диапазоном `[0,1]`. Lifecycle status: `active`, `closed`, `expired`; review его не меняет. `dispatcher_result` — convenience projection последнего review; authoritative review history возвращает `/predictions/{id}/reviews`.

`recommendations` — типизированный список `{prediction_id,code,title,description,source}`; source: `mock`, `rule`, `model`, `operator`.
Recommendation — инструкция, связанная с конкретным Prediction; она не входит в `factors`. Для локального maintenance draft клиент может передать связанный `prediction_id`; это не означает отправку заявки во внешнюю ремонтную систему.

Object и Channel возвращают согласованную четвёрку nullable-полей `risk_score`, `risk_level`, `risk_prediction_id`, `risk_score_kind`: либо все заполнены из одного активного прогноза, либо все `null`. Категории выбираются в порядке `critical > high > medium > low`, затем по новизне и ID. Object также возвращает `source_parent_id`, безопасный `parent_id` и `parent_reference_found`.

### PredictionReview

Запись проверки/решения по прогнозу: `id`, `prediction_id`, `decision`, nullable `reason_code`, `comment`, `reviewed_by`, UTC `reviewed_at`. Находящийся в своей области видимости техник или диспетчер может создать append-only запись; она не закрывает прогноз.

### PredictionOutcome

Подтверждённый позднее исход: `id`, `prediction_id`, `actual_outcome`, nullable `reason_code`, `comment`, `confirmed_by`, UTC `confirmed_at`. Outcome не является review и хранится отдельной append-only историей.

### MaintenanceRequest

Response содержит `object_id`, nullable `channel_id`/`prediction_id`, reason, priority, status, UTC timestamps и integration-owned поля `source`, `external_request_id`, `sync_status`, `external_status`. Create request принимает только `object_id`, optional `channel_id`/`prediction_id`, `reason` и `priority`; backend всегда создаёт local `draft` с `source=local`, `external_request_id=null`, `sync_status=not_synced` и `external_status=null`. Patch request принимает только `status`. Внешняя интеграция не подключена, и эти поля нельзя устанавливать пользовательским payload.

### Notification

`read` вычисляется из receipt текущего authenticated `user_id`; прочтение одним пользователем не меняет другого. Уведомление с `object_id` доступно только внутри object scope; `object_id=null` означает global/system notification и доступно только глобальному scope (central dispatcher). `severity` принимает `info`, `warning` или `critical`; Telegram не входит в core contract и может быть только внешним consumer этого REST API.

### AuditEvent

Все обращения к business API (включая GET/read, forbidden/unauthorized и просмотр audit journal) автоматически получают request audit запись с временем, actor/role если известны, HTTP method/path, действием, определимой сущностью, результатом, status code и внутренним correlation `request_id`. В публичном `action` method/path/result/status представлены одной строкой, чтобы сохранить существующую форму AuditEvent; nullable actor/entity поля обозначают неизвестную identity или неидентифицируемую сущность. Login success/failure журналируются отдельно; при неуспешном login сохраняется только username, без пароля. Значимые изменения дополнительно сохраняют domain events (`prediction.review.created`, `prediction.closed`, `prediction.outcome.confirmed`, `maintenance_request.created/updated`, `notification.read_state_changed`). Password, bearer token, Authorization header, cookies, secret/API keys и raw request body не журналируются. `/health`, docs/OpenAPI/static и служебные обращения исключены; внутренняя запись audit не создаёт новый audit event. Событие просмотра audit journal пишется после формирования ответа, поэтому видно при следующем чтении. Существующая форма полей ответа сохраняется (`id`, UTC `timestamp`, nullable `user_id`/`role`, `action`, `entity_type`, nullable `entity_id`, `object_id`).

В normal operational mode audit journal хранится в PostgreSQL как durable append-only история. Критические domain mutations и соответствующие domain audit records записываются в общей Unit of Work и фиксируются одной PostgreSQL-транзакцией: при rollback не остаётся ни изменения, ни его audit-записи. `GET /audit-events` read-only по отношению к журналу и выдаёт события только в пределах capability/object scope; сам факт чтения журналируется отдельным request audit после формирования ответа. Пароли, bearer tokens, cookies, заголовки авторизации, API keys, секреты и raw request bodies в audit не записываются.

Historical Replay — отдельная resettable demo-session на SQLite. Её сброс удаляет состояние этой демонстрационной сессии; она не заменяет durable operational persistence и audit retention normal PostgreSQL mode. Normal mode требует PostgreSQL и при его недоступности завершается fail-closed без перехода на RAM или SQLite. Локальный smoke с 20 клиентами выполнен для ограниченного тестового сценария; это не production capacity guarantee. Границы локальной проверки описаны в `REPLAY_LOAD_SMOKE.md` и `TEST_STATUS.md`.

## Endpoints

| Method | Path | Назначение |
|---|---|---|
| GET | `/` | service metadata |
| GET | `/health` | status и component health |
| POST | `/auth/login` | demo login, выдача bearer token |
| GET | `/auth/me` | identity, role и object scope текущего пользователя |
| GET | `/audit-events` | неизменяемый audit trail в доступном object scope |
| GET | `/dashboard` | согласованные агрегаты риска |
| GET | `/objects` | фильтры `search,type,risk_level`, pagination |
| GET | `/objects/{object_id}` | Object |
| GET | `/objects/{object_id}/channels` | Channels с batch `last_event_at` |
| GET | `/channels/{channel_id}` | Channel |
| GET | `/channels/{channel_id}/history` | `from,to,limit`; 503 если storage отсутствует |
| GET | `/channels/{channel_id}/predictions` | predictions канала |
| GET | `/predictions` | фильтры `from,to,object_id,channel_id,sensor_type,scope_type,incident_type,risk_level,status`, pagination |
| GET | `/predictions/{prediction_id}` | Prediction |
| POST | `/predictions/{prediction_id}/close` | закрыть активный prediction; повторный вызов не создаёт дополнительного lifecycle transition |
| PATCH | `/predictions/{prediction_id}/review` | добавить review; ответ `{"success":true}` |
| GET | `/predictions/{prediction_id}/reviews` | история reviews |
| POST | `/predictions/{prediction_id}/outcomes` | добавить подтверждённый outcome |
| GET | `/predictions/{prediction_id}/outcomes` | история outcomes |
| GET | `/maintenance-requests` | фильтры и pagination |
| POST | `/maintenance-requests` | создать local draft с проверкой ссылок |
| PATCH | `/maintenance-requests/{request_id}` | изменить только локальный lifecycle status |
| GET | `/notifications` | список и unread_count текущего получателя |
| PATCH | `/notifications/{notification_id}` | изменить receipt текущего получателя |

Maintenance возвращает 404 для отсутствующей ссылки и 409 для существующих, но конфликтующих object/channel/prediction.

## Request и list формы

Все list endpoints возвращают `{ "items": [...], "total": integer }`, кроме notifications: `{ "items": [...], "unread_count": integer }`. History возвращает `{ "channel_id": integer, "events": [...] }`.

Review request (actor устанавливается backend из bearer identity):

```json
{"dispatcher_result":"inspection_requested","reason_code":"preventive_check",
 "comment":"Проверить канал"}
```

Outcome request:

```json
{"actual_outcome":"incident_confirmed","reason_code":"inspection",
 "comment":"Подтверждено при выезде"}
```

`reviewed_at` и `confirmed_at` назначает backend в UTC; `reviewed_by` и `confirmed_by` берутся из authenticated user, клиент их не передаёт.

Maintenance create request:

```json
{"object_id":28,"channel_id":2866,"prediction_id":1042,"reason":"Проверить канал",
 "priority":"high"}
```

Maintenance response дополнительно вернёт server-owned integration defaults (`source=local`, `external_request_id=null`, `sync_status=not_synced`, `external_status=null`). Maintenance patch принимает только обязательный допустимый `status`; integration metadata может менять только будущий доверенный adapter. Notification patch: `{ "read": true }`.

Health содержит структурированные компоненты. Event history сообщает `expected_years`, `available_years` и status `available|partial|missing|unavailable|corrupt`; corruption существующего partition имеет приоритет над отсутствующим годом, затем отражается missing manifest, а только отсутствие ожидаемого годового partition означает `partial`. Reference data сообщает `available|unavailable|corrupt` и detail. Пример без Parquet:

```json
{"status":"degraded","service":"tgoat-api",
 "components":{"reference_data":{"status":"available","detail":null},
 "event_history":{"status":"unavailable","expected_years":[2019,2020,2021,2022,2023,2024,2025,2026],"available_years":[],"detail":"no event partitions"}}}
```

Пустая история при доступном storage возвращает 200 и `events=[]`; недоступный storage возвращает 503 с code `event_history_unavailable`.
