# Локальный запуск T.G.O.A.T.

## Проверенное окружение

- Python 3.12+ (Stage 13 проверялся на Python 3.12.3).
- Node.js `^20.19.0 || ^22.12.0 || >=24.0.0` — диапазон из текущего `frontend/package-lock.json`.
- PostgreSQL 16 для локального operational acceptance; SQL-контракт проекта совместим с PostgreSQL 12+.
- Historical Replay использует включённые SQLite-сессии и не требует PostgreSQL.

## Установка зависимостей

Из корня репозитория:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
(cd frontend && npm ci)
```

## Historical Replay

Откройте два терминала из корня проекта. Backend:

```sh
export TGOAT_RUNTIME_MODE=replay
export TGOAT_REPLAY_AUTO_START=false
export DEMO_AUTH_TOKEN_SECRET='replace-with-a-long-random-local-secret'
.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Frontend:

```sh
cd frontend
npm run dev -- --host 127.0.0.1
```

Интерфейс: <http://127.0.0.1:5173/>. API: <http://127.0.0.1:8000/api/v1>; OpenAPI: <http://127.0.0.1:8000/docs>. Dev server проксирует `/api` на `127.0.0.1:8000`.

Включённые demo/replay-данные: `ml/data/demo/replay/replay_manifest.json`, `hourly-state-2026-04-27--2026-05-07.jsonl.gz` и `replay-events-2026-05-05--2026-05-07.jsonl.gz`. Дополнительный bounded источник для simulated-live: `ml/data/demo/risk-runtime-events-2026-06-object-5343.csv`.

## Переключение на PostgreSQL operational mode

Создайте пустую базу на доступном PostgreSQL 16 (контракт поддерживает PostgreSQL 12+) и задайте подключение и локальный auth secret:

```sh
export TGOAT_RUNTIME_MODE=normal
export TGOAT_OPERATIONAL_STORAGE=postgres
export TGOAT_OPERATIONAL_DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/DB'
export DEMO_AUTH_TOKEN_SECRET='replace-with-a-long-random-local-secret'
```

Для доступности ML-прогнозов также задайте `TGOAT_FEATURE_STATE_DB` на полный совместимый feature-state SQLite-файл. Без него backend запускается с inference в fail-closed состоянии и не выдаёт прогнозы. При старте backend применяет PostgreSQL migrations; обычный runtime не переключается автоматически на SQLite или mock storage.

Запуск API:

```sh
.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Frontend запускается той же командой из раздела выше. Для TLS/Docker Compose конфигурации см. `docker/.env.example` и `docs/DEPLOYMENT.md`.

## Проверка запуска и остановка

В другом терминале:

```sh
curl -fsS http://127.0.0.1:8000/api/v1/health
curl -fsS -o /dev/null -w 'frontend HTTP %{http_code}\n' http://127.0.0.1:5173/
```

Успешный backend health response содержит `"status":"ok"`; интерфейс отвечает HTTP 200. Для остановки API и Vite нажмите `Ctrl+C` в соответствующих терминалах. Если PostgreSQL запускался отдельным Docker Compose сервисом, остановите его командой `docker compose --env-file docker/.env -f docker/compose.yml stop postgres`.
