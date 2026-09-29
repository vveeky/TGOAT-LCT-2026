def serialize_object(obj: dict, risk: dict, active_requests: int) -> dict:
    return {
        "id": obj["object_id"],
        "name": obj["object_name"],
        "type": obj["object_type"],
        "channels_count": obj["channels_count"],
        "risk_score": risk["risk_score"],
        "risk_level": risk["risk_level"],
        "risk_prediction_id": risk["risk_prediction_id"],
        "risk_score_kind": risk["risk_score_kind"],
        "active_predictions": risk["active_predictions"],
        "active_requests": active_requests,
        "parent_id": obj.get("parent_id"),
        "source_parent_id": obj.get("source_parent_id"),
        "parent_reference_found": obj.get("parent_reference_found", True),
        "level": obj.get("object_level"),
        "location_name": obj.get("object_name"),
        "geometry": None,
        "geometry_source": "not_provided",
    }


def serialize_channel(channel: dict, risk: dict, last_event_at: str | None) -> dict:
    return {
        "id": channel["channel_id"],
        "object_id": channel["object_id"],
        "sensor_type": channel["sensor_type"],
        "system_type": channel["system_type"],
        "name": channel["sensor_name"],
        "tag": channel["system_tag"],
        "risk_score": risk["risk_score"],
        "risk_level": risk["risk_level"],
        "risk_prediction_id": risk["risk_prediction_id"],
        "risk_score_kind": risk["risk_score_kind"],
        "last_event_at": last_event_at,
    }
