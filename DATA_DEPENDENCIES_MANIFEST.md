# Data dependencies

The public repository contains the frozen model and its manifest, product reference JSON, a bounded single-object event fixture and small Historical Replay fixtures. Ordinary installation and replay do not rebuild the model or require annual source datasets.

A complete historical rebuild or data audit additionally requires the annual `ext-journal-2019.csv` through `ext-journal-2026.csv` inputs and customer reference sources under `ml/data/raw/`, producing `ml/data/processed/*.parquet`. Those raw/processed data and runtime databases are intentionally outside this repository.

Normal operational mode uses PostgreSQL for persistent workflow and audit. Current forecasts also require a compatible, fresh feature-state provider supplied for that deployment. The checked-in bounded event fixture is historical and demonstrates model serving; it must not be presented as current live telemetry.
