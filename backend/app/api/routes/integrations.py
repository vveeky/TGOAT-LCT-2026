"""Safe operator-facing status of local integration adapters and simulated feed."""
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from backend.app.services.auth import AuthUser, get_current_user, require_capability
from backend.app.services.live_status import current_simulated_watermark

router = APIRouter(prefix='/integrations', tags=['Integration capability'])


@router.get('/status')
def integration_status(request: Request, user: AuthUser = Depends(get_current_user)):
    require_capability(user, 'evidence.read', 'management.read')
    database = getattr(request.app.state, 'operational_database', None)
    if database is None:
        return {'mode': 'historical_replay' if getattr(request.app.state, 'replay_runtime', None) else 'sqlite_test',
                'customer_smv_connected': False, 'simulated_feed': None}
    progress = current_simulated_watermark(database)
    return {'mode': 'simulated_live' if progress else 'normal',
            'customer_smv_connected': False,
            'simulated_feed': progress,
            'feed_kind': 'historical_registered_events_revealed_as_stream' if progress else None,
            'equipment_registry': 'local_reference_fixture',
            'external_customer_equipment_connected': False,
            'external_customer_helpdesk_connected': False}



def _live_service(request: Request):
    service = getattr(request.app.state, 'simulated_live_service', None)
    if service is None:
        raise HTTPException(409, 'Simulated live source is not enabled')
    return service


@router.get('/simulated-live/status')
def simulated_live_status(request: Request, user: AuthUser = Depends(get_current_user)):
    require_capability(user, 'evidence.read', 'management.read', 'access.manage')
    return _live_service(request).status()


@router.post('/simulated-live/step')
def simulated_live_step(request: Request, user: AuthUser = Depends(get_current_user)):
    require_capability(user, 'replay.control', 'access.manage')
    return _live_service(request).process_one()


@router.post('/simulated-live/start')
def simulated_live_start(request: Request, user: AuthUser = Depends(get_current_user)):
    require_capability(user, 'replay.control', 'access.manage')
    return _live_service(request).resume()


@router.post('/simulated-live/pause')
def simulated_live_pause(request: Request, user: AuthUser = Depends(get_current_user)):
    require_capability(user, 'replay.control', 'access.manage')
    return _live_service(request).pause()


@router.get('/equipment')
def local_equipment_registry(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: AuthUser = Depends(get_current_user),
):
    """Read-only equipment-registry adapter over the supplied local fixture.

    This is a functional API contract, NOT a link to the customer registry.
    Scope and total are filtered before pagination to avoid ID/count leakage.
    """
    from backend.app.services.auth import accessible_object_ids
    from backend.app.services.reference_data import get_objects_data, get_channels_by_object, get_reference_meta
    from backend.app.services.equipment_reference import CUSTOMER_EQUIPMENT_TERMINOLOGY
    require_capability(user, 'objects.read', 'evidence.read', 'management.read')
    allowed = accessible_object_ids(user)
    scope = set(allowed) if allowed is not None else None
    objects = [item for item in get_objects_data()
               if scope is None or int(item['object_id']) in scope]
    objects.sort(key=lambda item: item['object_id'])
    channel_index = get_channels_by_object()
    return {
        'source_kind': 'customer_supplied_static_reference_fixture',
        'external_customer_connection': False,
        'read_only': True,
        'reference_sha256': get_reference_meta()['source_sha256'],
        'customer_confirmed_terminology': CUSTOMER_EQUIPMENT_TERMINOLOGY,
        'total': len(objects),
        'limit': limit, 'offset': offset,
        'items': [{**item, 'channels': channel_index.get(item['object_id'], [])}
                  for item in objects[offset:offset + limit]],
    }


@router.get('/work-status/{request_id}')
def local_work_status(request_id: int, request: Request,
                      user: AuthUser = Depends(get_current_user)):
    """Read-only adapter for the application's own persisted Request lifecycle."""
    from backend.app.api.routes.signals import workflow
    from backend.app.services.dispatcher_workflow import WorkflowNotFound
    require_capability(user, 'requests.read')
    try:
        detail = workflow(request).request_detail(request_id, user)
    except WorkflowNotFound as error:
        raise HTTPException(404, 'Request not found') from error
    return {'source_kind': 'internal_workflow_not_customer_helpdesk',
            'external_customer_connection': False, 'read_only': True, 'request': detail}


@router.get('/weather')
def city_weather(request: Request, user: AuthUser = Depends(get_current_user)):
    """Published city-wide evidence only; never bind city forecast to fake object GPS."""
    import os
    from datetime import datetime, timezone
    from backend.app.services.telemetry_clock import telemetry_upper_bound
    from backend.app.services.weather_context import load_weather_context
    require_capability(user, 'evidence.read', 'management.read')
    as_of = telemetry_upper_bound(request) or datetime.now(timezone.utc)
    return load_weather_context(os.environ.get('TGOAT_WEATHER_EVENTS_FILE'), as_of)
