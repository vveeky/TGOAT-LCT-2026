# T.G.O.A.T. — LCT 2026

T.G.O.A.T. — прототип системы поддержки решений для диспетчеризации объектов ЖКХ. Он показывает зарегистрированные сигналы, рассчитывает прогноз по замороженной ML-модели, помогает диспетчеру оформить решение и довести заявку до результата техника.

## Возможности

- Прогноз по 87 признакам на горизонте 24 часов, карточки риска и история по объектам.
- Маршрут Signal → Request → назначение → результат техника → приёмка диспетчером → закрытие.
- Разграничение доступа по ролям и области объектов; журнал аудита действий.
- Панели мониторинга, уведомления, контекст датчиков и демонстрационная карта. Координаты карты синтетические.
- Historical Replay с управляемым виртуальным временем и ограниченным набором включённых данных.

**ML-оценка означает generic 24-hour proxy alarm/anomaly risk. Это не подтверждённая вероятность физического пожара, подтопления, проникновения или поломки.** Подключение к рабочим системам Заказчика и production LDAP/AD не заявляется.

## Архитектура и стек

React/Vite → nginx (HTTPS) → FastAPI → PostgreSQL для обычных прогнозов, workflow, уведомлений и append-only аудита. Критические изменения и соответствующие записи аудита фиксируются в общей транзакции. Feature-state для модели хранится отдельно; Historical Replay использует изолированную сбрасываемую SQLite demo-session. Модель загружается из включённого frozen artifact с проверкой SHA-256.

Стек: Python 3.12, FastAPI, scikit-learn, PostgreSQL 16, React, Vite, nginx, Docker Compose v2.

## Запуск через Docker Compose

Для обычного режима нужны Docker Engine, Compose v2, TLS-сертификат и заданные локально секреты:

```sh
./scripts/generate_dev_tls.sh ./docker/.local-tls
cp docker/.env.example docker/.env
# В docker/.env задайте сильные TGOAT_POSTGRES_PASSWORD и TGOAT_AUTH_SECRET,
# а также TGOAT_CERT_DIR=./.local-tls для локального сертификата.
cd docker
docker compose --env-file .env -f compose.yml up -d --build postgres backend web
```

Локальный адрес: `https://localhost:8443/` (самоподписанный сертификат предназначен только для разработки). Конфигурация normal mode требует PostgreSQL и не переключается автоматически на RAM или SQLite. Для актуальных ML-прогнозов нужен совместимый свежий feature-state; без него API явно показывает `degraded`/`no_prediction`. Годовые CSV и Parquet для запуска не нужны. Подробности: [развёртывание](docs/DEPLOYMENT.md), [установка](docs/INSTALLATION.md), [локальный запуск](LOCAL_STARTUP.md), [backend runbook](docs/BACKEND_RUNBOOK.md).

## Демонстрация и данные

Рекомендуемый сценарий — [Historical Replay](docs/DEMO_RUNBOOK.md): отдельный режим с ограниченным историческим окном, виртуальным временем и сбрасываемой сессией. Он использует `ml/data/demo/replay/replay_manifest.json` и два `.jsonl.gz` файла рядом с ним. В репозитории также находятся `ml/artifacts/risk_baseline_v1/phase2_hgb.joblib`, `phase2_manifest.json`, справочники `backend/data/*.json` и небольшой bounded fixture для проверки feature-state. Полные годовые журналы, processed Parquet и базы данных в публичный репозиторий не входят. Дополнительные источники данных задаются отдельно; сведения о границах поставки — в [манифесте данных](DATA_DEPENDENCIES_MANIFEST.md).

Демо-роли: центральный и районный диспетчеры, техник и администратор доступа. Локальные учётные записи и ограничения описаны в [контракте API](docs/API_CONTRACT.md); тестовые пароли предназначены только для локальной демонстрации.

## Документация

- [Архитектура](docs/ARCHITECTURE.md) · [API](docs/API_CONTRACT.md) · [контракт данных](docs/DATA_CONTRACT.md)
- [ML model card](docs/ML_MODEL_CARD.md) · [демо](docs/DEMO_RUNBOOK.md) · [ограничения](docs/LIMITATIONS.md)
- [Безопасность и роли](docs/SECURITY_RBAC.md) · [аудит](docs/AUDIT_TRAIL.md) · [статус проверок](docs/TEST_STATUS.md)
