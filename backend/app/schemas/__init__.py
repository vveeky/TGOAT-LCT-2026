import math
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

RiskLevel = Literal["low", "medium", "high", "critical"]
PredictionStatus = Literal["active", "closed", "expired"]
DispatcherResult = Literal["confirmed", "false_alarm", "inspection_requested", "monitoring"]
MaintenanceStatus = Literal["draft", "new", "assigned", "in_progress", "awaiting_review", "closed", "cancelled"]
ScopeType = Literal["channel", "object", "engineering_system"]
ScoreKind = Literal["probability", "anomaly_score", "rule_score", "proxy_probability", "mock"]


class Object(BaseModel):
    id: int
    name: str
    type: str | None
    channels_count: int
    risk_score: float | None
    risk_level: RiskLevel | None
    risk_prediction_id: int | None
    risk_score_kind: ScoreKind | None
    active_predictions: int
    active_requests: int
    parent_id: int | None = None
    source_parent_id: int | None = None
    parent_reference_found: bool = True
    level: int | None = None
    location_name: str | None = None
    geometry: dict[str, Any] | None = None
    geometry_source: Literal["real", "synthetic_mvp", "unknown", "not_provided"] = "not_provided"

    @model_validator(mode="after")
    def validate_risk_provenance(self):
        values = (self.risk_score, self.risk_level, self.risk_prediction_id, self.risk_score_kind)
        if any(value is None for value in values) and not all(value is None for value in values):
            raise ValueError("risk fields must all be null or all be populated")
        return self

    @model_validator(mode="after")
    def validate_geometry_provenance(self):
        if self.geometry is None and self.geometry_source in {"real", "synthetic_mvp"}:
            raise ValueError("geometry source requires geometry")
        if self.geometry is not None and self.geometry_source not in {"real", "synthetic_mvp"}:
            raise ValueError("geometry requires real or synthetic_mvp source")
        return self


class ObjectListResponse(BaseModel):
    items: list[Object]
    total: int


class RiskForecastResponse(BaseModel):
    object_id: int
    snapshot_ts: AwareDatetime
    generated_at: AwareDatetime
    risk_probability: float | None = None
    risk_flag: bool | None = None
    threshold: float | None = None
    model_version: str | None = None
    feature_schema_version: str | None = None
    status: Literal["available", "degraded"]
    risk_score_kind: Literal["ml"]
    degraded_reason: str | None = None
    reference_snapshot_id: str | None = None
    reference_hash: str | None = None
    source_freshness_ts: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_status_semantics(self):
        if self.status == "available" and self.risk_probability is None:
            raise ValueError("available forecast requires risk_probability")
        if self.status == "degraded" and (self.risk_probability is not None or self.risk_flag is not None):
            raise ValueError("degraded forecast cannot contain a score")
        return self


class OperationalPredictionHistory(BaseModel):
    identity: str
    object_id: int
    snapshot_ts: AwareDatetime
    generated_at: AwareDatetime
    risk_probability: float | None = None
    risk_flag: bool | None = None
    threshold: float | None = None
    prediction_status: Literal["available", "degraded"]
    degraded_reason: str | None = None
    risk_score_kind: Literal["ml"]
    model_version: str | None = None
    feature_schema_version: str | None = None
    reference_snapshot_id: str | None = None
    reference_hash: str | None = None
    source_freshness_ts: AwareDatetime | None = None
    created_at: AwareDatetime


class ForecastCard(BaseModel):
    card_id: str
    object_id: int
    first_positive_snapshot_ts: AwareDatetime
    last_positive_snapshot_ts: AwareDatetime
    max_probability: float
    threshold: float | None = None
    model_version: str | None = None
    feature_schema_version: str | None = None
    reference_snapshot_id: str | None = None
    reference_hash: str | None = None
    status: Literal["active", "uncertain", "closed"]
    created_at: AwareDatetime
    updated_at: AwareDatetime


class RecommendationResponse(BaseModel):
    recommendation_id: str
    object_id: int
    snapshot_ts: AwareDatetime
    card_id: str | None = None
    status: Literal["available", "degraded", "not_required"]
    code: str | None = None
    action: str | None = None
    basis: list[str]
    rule_version: str
    generated_at: AwareDatetime
    model_version: str | None = None
    reference_hash: str | None = None


class OperationalRiskStateResponse(BaseModel):
    object_id: int
    prediction_identity: str | None = None
    status: Literal["available", "degraded", "no_prediction"]
    snapshot_ts: AwareDatetime | None = None
    generated_at: AwareDatetime | None = None
    risk_probability: float | None = None
    risk_flag: bool | None = None
    threshold: float | None = None
    model_version: str | None = None
    feature_schema_version: str | None = None
    risk_score_kind: Literal["ml"]
    degraded_reason: str | None = None
    card: ForecastCard | None = None
    recommendation: RecommendationResponse | None = None


class OperationalDashboardResponse(BaseModel):
    cycle_status: Literal["completed", "stale", "failed", "unavailable", "no_cycle"]
    stale: bool
    cycle_id: str | None = None
    cycle_completed_at: AwareDatetime | None = None
    latest_attempted_snapshot_ts: AwareDatetime | None = None
    latest_attempted_status: str | None = None
    snapshot_ts: AwareDatetime | None = None
    object_count: int
    available_count: int
    positive_count: int
    negative_count: int
    degraded_count: int
    no_prediction_count: int
    items: list[OperationalRiskStateResponse]


class OperationalPredictionJournalItem(BaseModel):
    identity: str
    object_id: int
    snapshot_ts: AwareDatetime
    generated_at: AwareDatetime
    prediction_status: Literal["available", "degraded"]
    risk_probability: float | None
    risk_flag: bool | None
    risk_score_kind: Literal["ml"]
    model_version: str | None
    feature_schema_version: str | None
    review_count: int
    latest_review_decision: str | None
    outcome_count: int
    latest_actual_outcome: str | None


class OperationalPredictionJournalResponse(BaseModel):
    items: list[OperationalPredictionJournalItem]
    total: int
    limit: int
    offset: int


class OperationalReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: DispatcherResult
    reason_code: str | None = Field(default=None, max_length=100)
    comment: str | None = Field(default=None, max_length=2000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)


class OperationalReviewResponse(BaseModel):
    id: int
    prediction_identity: str
    object_id: int
    snapshot_ts: AwareDatetime
    actor_user_id: str
    actor_role: str
    decision: DispatcherResult
    reason_code: str | None
    comment: str | None
    created_at: AwareDatetime
    idempotency_key: str


class OperationalOutcomeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actual_outcome: str = Field(min_length=1, max_length=200)
    reason_code: str | None = Field(default=None, max_length=100)
    comment: str | None = Field(default=None, max_length=2000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)


class OperationalOutcomeResponse(BaseModel):
    id: int
    prediction_identity: str
    object_id: int
    snapshot_ts: AwareDatetime
    actor_user_id: str
    actor_role: str
    actual_outcome: str
    reason_code: str | None
    comment: str | None
    created_at: AwareDatetime
    idempotency_key: str


class Channel(BaseModel):
    latest_value: str | None = None
    latest_alarm: bool | None = None
    id: int
    object_id: int | None
    sensor_type: str | None
    system_type: str | None
    name: str | None
    tag: str | None
    risk_score: float | None
    risk_level: RiskLevel | None
    risk_prediction_id: int | None
    risk_score_kind: ScoreKind | None
    last_event_at: AwareDatetime | None

    @model_validator(mode="after")
    def validate_risk_provenance(self):
        values = (self.risk_score, self.risk_level, self.risk_prediction_id, self.risk_score_kind)
        if any(value is None for value in values) and not all(value is None for value in values):
            raise ValueError("risk fields must all be null or all be populated")
        return self


class ChannelListResponse(BaseModel):
    items: list[Channel]
    total: int


class ChannelHistoryEvent(BaseModel):
    timestamp: AwareDatetime
    alarm: bool | None
    value: str | None


class ChannelHistoryResponse(BaseModel):
    channel_id: int
    events: list[ChannelHistoryEvent]


class Recommendation(BaseModel):
    prediction_id: int
    code: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source: Literal["mock", "rule", "model", "operator"]


class Prediction(BaseModel):
    id: int
    scope_type: ScopeType
    incident_type: str = Field(min_length=1)
    object_id: int
    channel_id: int | None = None
    system_ref: str | None = None
    score_kind: ScoreKind
    model_version: str | None = None
    risk_score: float
    risk_level: RiskLevel
    forecast_from_hours: int = Field(ge=0)
    forecast_to_hours: int = Field(ge=0)
    factors: list[str]
    created_at: AwareDatetime
    status: PredictionStatus
    dispatcher_result: DispatcherResult | None = None
    recommendations: list[Recommendation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_scope_and_forecast(self):
        if self.scope_type == "channel" and self.channel_id is None:
            raise ValueError("channel scope requires channel_id")
        if self.scope_type == "engineering_system" and not self.system_ref:
            raise ValueError("engineering_system scope requires system_ref")
        if self.forecast_to_hours < self.forecast_from_hours:
            raise ValueError("forecast_to_hours must be >= forecast_from_hours")
        if not math.isfinite(self.risk_score):
            raise ValueError("risk_score must be finite")
        if self.score_kind in {"probability", "proxy_probability"} and not 0 <= self.risk_score <= 1:
            raise ValueError("probability scores must be between 0 and 1")
        if any(item.prediction_id != self.id for item in self.recommendations):
            raise ValueError("recommendation prediction_id must match prediction id")
        return self


class PredictionListResponse(BaseModel):
    items: list[Prediction]
    total: int


class PredictionReviewRequest(BaseModel):
    dispatcher_result: DispatcherResult
    reason_code: str | None = None
    comment: str | None = None


class PredictionReview(BaseModel):
    id: int
    prediction_id: int
    decision: DispatcherResult
    reason_code: str | None = None
    comment: str | None = None
    reviewed_by: str | None = None
    reviewed_at: AwareDatetime


class PredictionReviewListResponse(BaseModel):
    items: list[PredictionReview]
    total: int


class PredictionOutcomeCreate(BaseModel):
    actual_outcome: str = Field(min_length=1)
    reason_code: str | None = None
    comment: str | None = None


class PredictionOutcome(BaseModel):
    id: int
    prediction_id: int
    actual_outcome: str
    reason_code: str | None = None
    comment: str | None = None
    confirmed_by: str | None = None
    confirmed_at: AwareDatetime


class PredictionOutcomeListResponse(BaseModel):
    items: list[PredictionOutcome]
    total: int


class SuccessResponse(BaseModel):
    success: bool


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(gt=0)


class CurrentUserResponse(BaseModel):
    user_id: str
    username: str
    role: str
    roles: list[str] = Field(default_factory=list)
    grants: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    scope_roots: list[int] | None


class MaintenanceRequest(BaseModel):
    id: int
    object_id: int | None
    channel_id: int | None
    prediction_id: int | None
    reason: str
    priority: str
    status: MaintenanceStatus
    source: str = "local"
    external_request_id: str | None = None
    sync_status: str | None = "not_synced"
    external_status: str | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    card_id: str | None = None
    prediction_identity: str | None = None
    decision_id: int | None = None
    created_by_user_id: str | None = None
    created_by_role: str | None = None
    comment: str | None = None
    origin: Literal["manual", "signal"] = "manual"
    version: int = 1
    command_key: str | None = None
    assignee_user_id: str | None = None
    assigned_by_user_id: str | None = None
    assigned_at: AwareDatetime | None = None
    started_at: AwareDatetime | None = None
    accepted_by_user_id: str | None = None
    accepted_at: AwareDatetime | None = None
    latest_event: str | None = None


class MaintenanceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_id: int
    channel_id: int | None = None
    prediction_id: int | None = None
    reason: str = Field(min_length=1)
    priority: str = Field(min_length=1)

class MaintenanceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: MaintenanceStatus


class MaintenanceListItem(MaintenanceRequest):
    object_name: str


class MaintenanceListResponse(BaseModel):
    items: list[MaintenanceListItem]
    total: int


class Notification(BaseModel):
    id: int
    source_kind: Literal["operational_ml", "legacy_mock"] = "legacy_mock"
    type: str
    severity: Literal["info", "warning", "critical"] = "warning"
    title: str
    message: str
    prediction_id: int | str | None
    object_id: int | None
    channel_id: int | None
    card_id: str | None = None
    read: bool
    created_at: AwareDatetime


class NotificationUpdate(BaseModel):
    read: bool


class NotificationListResponse(BaseModel):
    items: list[Notification]
    unread_count: int


class AuditEvent(BaseModel):
    id: int
    timestamp: AwareDatetime
    user_id: str | None = None
    role: str | None = None
    actor_roles: list[str] = Field(default_factory=list)
    actor_scope: dict[str, Any] | None = None
    request_id: str | None = None
    action: str = Field(min_length=1)
    entity_type: str = Field(min_length=1)
    entity_id: str | None = None
    object_id: int | None = None
    outcome: str | None = None
    failure_reason: str | None = None
    source_mode: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AccessAssignmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roles: list[Literal["technician", "district_dispatcher", "central_dispatcher"]]
    grants: list[Literal["management.read", "evidence.read", "model.explain"]] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    expected_version: int = Field(ge=0)


class AccessPrincipalResponse(BaseModel):
    user_id: str
    username: str
    role: str
    roles: list[str]
    grants: list[str]
    groups: list[str]
    scope_roots: list[int] | None
    version: int = 0


class AuditEventListResponse(BaseModel):
    items: list[AuditEvent]
    total: int


class RiskDistribution(BaseModel):
    low: int
    medium: int
    high: int
    critical: int


class DashboardResponse(BaseModel):
    objects_count: int
    critical_risk_count: int
    high_risk_count: int
    active_requests_count: int
    risk_distribution: RiskDistribution
    top_objects: list[Object]
    recent_predictions: list[Prediction]


class ReferenceDataHealth(BaseModel):
    status: Literal["available", "unavailable", "corrupt"]
    detail: str | None = None


class EventHistoryHealth(BaseModel):
    status: Literal["available", "partial", "unavailable", "corrupt", "missing"]
    expected_years: list[int]
    available_years: list[int]
    detail: str | None = None


class ComponentHealth(BaseModel):
    reference_data: ReferenceDataHealth
    event_history: EventHistoryHealth


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    service: str
    components: ComponentHealth
