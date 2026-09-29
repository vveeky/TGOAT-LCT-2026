# Frozen ML model card

Artifact: `ml/artifacts/risk_baseline_v1/phase2_hgb.joblib`

SHA-256: `223b313953500c54678a495f65bc43e1e6a6f0ada38b8cd5ea267da86910ef76`

Model: HistGradientBoostingClassifier. Ordered feature contract: 87 features. Decision threshold: `0.39877346430366006`. Forecast horizon: next 24 hours.

Training years: 2019, 2020, 2022, 2023. 2021 is treated as a transition period; 2024 validation, 2025 independent test, 2026 recent/demo context are kept separate by the accepted ML reports.

The target is a proxy alarm/anomaly onset, not a confirmed physical incident. The application therefore does not manufacture `P(fire)`, `P(flooding)` or `P(intrusion)` from the generic score. Fire/intrusion/flooding views are evidence-based decision-support scenarios.

The artifact, feature order, target and threshold are frozen. Stage 6 planned-work analysis did not justify retraining because the 2026 schedules cannot be safely cross-walked to the pseudonymized object IDs and do not cover the training years.
