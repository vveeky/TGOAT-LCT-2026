import polars as pl


PROCESSED_SCHEMA = {
    "event_id_raw": pl.String, "channel_id_raw": pl.String, "date": pl.String,
    "time": pl.String, "alarm_raw": pl.String, "sensor_value": pl.String,
    "event_id": pl.Int64, "channel_id": pl.Int64, "timestamp": pl.Datetime("us"),
    "source_year": pl.Int32, "is_alarm": pl.Boolean, "invalid_alarm": pl.Boolean,
    "system_type": pl.String, "sensor_type": pl.String, "system_tag": pl.String,
    "sensor_name": pl.String, "object_id": pl.Int64,
    "channel_reference_found": pl.Boolean, "object_level": pl.Int64,
    "parent_id": pl.Int64, "object_type": pl.String, "object_name": pl.String,
    "object_reference_found": pl.Boolean, "invalid_channel_id": pl.Boolean,
    "invalid_event_id": pl.Boolean, "invalid_timestamp": pl.Boolean,
}

PROCESSED_NON_NULL_COLUMNS = {
    "source_year", "invalid_alarm", "channel_reference_found", "object_reference_found",
    "invalid_channel_id", "invalid_event_id", "invalid_timestamp",
}
