from backend.app.services.mock_storage import maintenance_repository, prediction_repository


def object_risks(object_ids: list[int]) -> dict[int, dict]:
    return prediction_repository.object_risk_summaries(object_ids)


def channel_risks(channel_ids: list[int]) -> dict[int, dict]:
    return prediction_repository.channel_risk_summaries(channel_ids)


def active_request_counts(object_ids: list[int]) -> dict[int, int]:
    return maintenance_repository.active_counts_by_object(object_ids)
