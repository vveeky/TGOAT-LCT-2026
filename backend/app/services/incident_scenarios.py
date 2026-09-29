"""Evidence-based scenario analysis, separate from frozen ML probability.

``is_alarm`` is source telemetry saying that an alarm message was registered.
It never confirms a physical incident or dictates dispatch of a repair crew.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Mapping

from backend.app.config import as_utc
from backend.app.services.equipment_reference import CUSTOMER_EQUIPMENT_TERMINOLOGY

SCENARIO_TYPES = (
    'fire', 'flooding', 'gas', 'intrusion', 'anomalous_temperature',
    'communication_loss', 'power_loss',
)
LIFE_SAFETY_TYPES = frozenset({'fire', 'flooding', 'gas', 'intrusion', 'anomalous_temperature'})
SENSOR_FAMILIES = {
    'fire': ('датчик дыма', 'тепловой датчик', 'ручной извещатель', 'уир-р'),
    'intrusion': ('датчик движения', 'кд дверь', 'кд люк', 'кд ав', 'состояние охраны', 'стекло', 'секционный люк'),
    'flooding': ('датчик затопления', 'датчик протечки', 'состояние насоса'),
    'gas': ('газовый датчик', 'загазованность'),
    'anomalous_temperature': ('датчик температуры', 'аномальная температура'),
    'communication_loss': ('потеря связи', 'нет связи', 'состояние связи', 'канал связи'),
    'power_loss': ('потеря питания', 'отсутствие питания', 'аварийное питание', 'состояние фазы'),
}
LABELS = {
    'fire': 'Пожарные признаки',
    'intrusion': 'Признаки доступа',
    'flooding': 'Признаки подтопления',
    'gas': 'Признаки загазованности',
    'anomalous_temperature': 'Аномальная температура',
    'communication_loss': 'Потеря связи и наблюдаемости',
    'power_loss': 'Признаки нарушения электроснабжения',
}
WINDOW = timedelta(days=7)
FRESH = timedelta(hours=6)


def relevant_channel(scenario_type: str, sensor_type: str, system_type: str = '') -> bool:
    """Match only explicit catalogue taxonomy; do not infer a diagnosis."""
    if scenario_type not in SENSOR_FAMILIES:
        raise ValueError('unsupported scenario type')
    sensor = sensor_type.casefold()
    system = system_type.casefold()
    if scenario_type == 'fire' and 'температурная подсистема' in system:
        return False
    if scenario_type == 'anomalous_temperature' and 'тепловой датчик' in sensor:
        return False
    if scenario_type == 'fire' and 'пожарная охрана' in system:
        return True
    if scenario_type == 'intrusion' and 'охранная подсистема' in system:
        return True
    if scenario_type == 'gas' and 'газовая охрана' in system:
        return True
    if scenario_type == 'anomalous_temperature' and 'температурная подсистема' in system:
        return True
    if scenario_type == 'communication_loss' and 'связ' in system:
        return True
    if scenario_type == 'power_loss' and any(word in system for word in ('электроснаб', 'электропит', 'питания')):
        return True
    return any(word in sensor for word in SENSOR_FAMILIES[scenario_type])


def _timestamp(value):
    return as_utc(datetime.fromisoformat(value.replace('Z', '+00:00'))
                  if isinstance(value, str) else value)


def _intrusion_route(*, selected, observations, as_of, history_source):
    by_channel = {int(channel['channel_id']): channel for channel in selected}
    cutoff = as_of - WINDOW
    alarms = []
    for observation in observations:
        channel_id = int(observation['channel_id'])
        if channel_id not in by_channel or observation.get('alarm') is not True:
            continue
        timestamp = _timestamp(observation['timestamp'])
        if cutoff <= timestamp <= as_of:
            channel = by_channel[channel_id]
            # Only explicit structured reference fields qualify as spatial data.
            point = {'channel_id': channel_id,
                     'sensor_name': channel.get('sensor_name'),
                     'timestamp': timestamp.isoformat()}
            if isinstance(channel.get('picket'), (str, int, float)):
                point['picket'] = channel['picket']
                point['location_source'] = 'channel_reference.picket'
            coords = channel.get('coordinates')
            if isinstance(coords, Mapping) and all(isinstance(coords.get(k), (int, float)) for k in ('lat', 'lon')):
                point['coordinates'] = {'lat': coords['lat'], 'lon': coords['lon']}
                point['location_source'] = 'channel_reference.coordinates'
            alarms.append(point)
    alarms.sort(key=lambda point: (point['timestamp'], point['channel_id']))
    has_locations = all('coordinates' in point or 'picket' in point for point in alarms)
    if not alarms:
        status = 'no_alarm_sequence_observed'
    elif has_locations:
        status = 'ordered_points_available'
    else:
        status = 'ordered_sensor_sequence_only'
    completeness = {
        'historical_replay': 'complete_within_selected_replay_window',
        'simulated_live': 'bounded_ingested_event_window',
        'annual_event_history_latest_only': 'incomplete_latest_state_per_channel_only',
        'unavailable': 'unavailable',
    }.get(history_source, 'unknown')
    limitation = ('Топология коллектора и маршрут между точками не подтверждены; показана только временная последовательность.'
                  if alarms and not has_locations else
                  'Последовательность ограничена доступными событиями и не доказывает физический маршрут.'
                  if alarms else 'В доступной выборке тревожная последовательность не обнаружена; отсутствие событий не доказано.')
    return {'status': status, 'points': alarms, 'route_geometry': None,
            'sequence_completeness': completeness, 'window_start': cutoff.isoformat(),
            'as_of': as_of.isoformat(), 'limitation': limitation}


def analyze_scenario(*, scenario_type: str, object_id: int, as_of: datetime,
                     channels: Iterable[Mapping], observations: Iterable[Mapping],
                     history_source: str, planned_work: Mapping,
                     generic_risk: Mapping | None = None, weather: Mapping | None = None) -> dict:
    """Project bounded evidence. Future observations never enter any output."""
    if scenario_type not in SCENARIO_TYPES:
        raise ValueError('unsupported scenario type')
    if as_of.tzinfo is None:
        raise ValueError('as_of must be timezone-aware')
    as_of = as_utc(as_of)
    observations = tuple(observations)
    selected = sorted((dict(item) for item in channels if relevant_channel(
        scenario_type, str(item.get('sensor_type') or ''), str(item.get('system_type') or ''))),
        key=lambda item: int(item['channel_id']))
    by_channel = {int(item['channel_id']): item for item in selected}
    previous: dict[int, dict] = {}
    counts = {key: {'sampled_observations': 0, 'sampled_alarms': 0} for key in by_channel}
    cutoff = as_of - WINDOW
    for observation in observations:
        channel_id = int(observation['channel_id'])
        if channel_id not in by_channel:
            continue
        timestamp = _timestamp(observation['timestamp'])
        if timestamp > as_of or timestamp < cutoff:
            continue
        counts[channel_id]['sampled_observations'] += 1
        counts[channel_id]['sampled_alarms'] += int(observation.get('alarm') is True)
        old = previous.get(channel_id)
        if old is None or timestamp >= old['parsed_at']:
            previous[channel_id] = {'parsed_at': timestamp,
                                    'alarm': observation.get('alarm'),
                                    'value': observation.get('value'),
                                    'source': observation.get('source', history_source)}
    evidence = []
    active = []
    missing = []
    observed = set()
    for item in selected:
        cid = int(item['channel_id'])
        last = previous.get(cid)
        if last is None:
            state = 'no_observation_in_window'
            missing.append(f'Нет зарегистрированной телеметрии канала {cid} за 7 дней')
        elif as_of - last['parsed_at'] > FRESH:
            state = 'stale'
            missing.append(f'Нет свежего подтверждения состояния канала {cid} (старше 6 ч)')
        elif last['alarm'] is True:
            state = 'alarm_observed'
            active.append(cid)
        elif last['alarm'] is False:
            state = 'non_alarm_observed'
        else:
            state = 'unknown_alarm_state'
            missing.append(f'Тревожность последнего события канала {cid} неизвестна')
        if last and as_of - last['parsed_at'] <= FRESH:
            observed.add(cid)
        evidence.append({'channel_id': cid, 'sensor_type': item['sensor_type'],
                         'sensor_name': item.get('sensor_name'), 'system_type': item.get('system_type'),
                         'state': state,
                         'latest_at': last['parsed_at'].isoformat() if last else None,
                         'latest_value': last['value'] if last else None,
                         'latest_alarm': last['alarm'] if last else None,
                         'source': last['source'] if last else None,
                         **counts[cid]})
    if not selected:
        missing.append('В реестре объекта нет датчиков для этого сценария')
    if history_source == 'unavailable':
        missing.append('Полный источник событий недоступен; отсутствие тревоги не подтверждено')
    if scenario_type == 'flooding':
        has_pump = any('насоса' in item['sensor_type'].casefold() for item in selected)
        has_flood = any('затопления' in item['sensor_type'].casefold() for item in selected)
        if not has_pump: missing.append('Нет канала состояния насоса')
        if not has_flood: missing.append('Нет канала обнаружения затопления')
        if weather is None or weather.get('status') != 'available':
            missing.append('Метеоконтекст не подключён/недоступен')
    if scenario_type == 'fire' and not any('дыма' in item['sensor_type'].casefold() for item in selected):
        missing.append('Нет дымового датчика в реестре этого объекта')
    planned = {key: list(planned_work.get(key, ())) for key in ('active', 'recent', 'upcoming')}
    if planned_work.get('unmapped_records'):
        missing.append('ППР заказчика содержит непривязанные объекты: отсутствие работ не доказано')
    recommendation = []
    if active:
        recommendation.append({'code': 'verify_sensors',
                               'title': 'Сверить показания тревожных каналов',
                               'basis_channels': active,
                               'detail': 'Зарегистрировано тревожное сообщение. Оно само по себе не подтверждает аварию и не означает обязательную отправку ремонтной бригады; диспетчер или дежурный инженер должен проверить основание.'})
        recommendation.append({'code': 'consider_inspection', 'title': 'Рассмотреть направление проверки',
                               'basis_channels': active, 'detail': 'По результатам проверки основание может быть квалифицировано как инцидент, профилактические/технические работы или ложная сработка. Решение принимает диспетчер.'})
    else:
        recommendation.append({'code': 'verify_availability', 'title': 'Проверить полноту и актуальность данных',
                               'basis_channels': [],
                               'detail': 'Без свежей телеметрии отсутствие признаков инцидента не доказано.'})
    if planned['active'] or planned['recent'] or planned_work.get('unmapped_records'):
        recommendation.append({'code': 'check_planned_work', 'title': 'Сопоставить с плановыми работами',
                               'basis_channels': active,
                               'detail': 'ППР означает планово-предупредительные работы, ТО — техническое обслуживание. Серия тревожных сообщений не доказывает проведение ППР; для связи требуется запись РТЭКК и подтверждённая привязка.'})
    if scenario_type == 'flooding':
        recommendation.append({'code': 'verify_weather_pumps', 'title': 'Сверить насосы и метеоисточник',
                               'basis_channels': [x['channel_id'] for x in selected if 'насоса' in x['sensor_type'].casefold()],
                               'detail': 'Если частота включений/прогноз осадков отсутствуют, их нельзя восстанавливать предположением.'})
    fresh_observation_ratio = len(observed) / len(selected) if selected else None
    if scenario_type in {'communication_loss', 'power_loss'}:
        incident_domain = 'operational_disruption'
    else:
        incident_domain = 'life_safety'
    if selected and fresh_observation_ratio == 0:
        observability_status = 'loss_indicated'
    elif selected and fresh_observation_ratio == 1:
        observability_status = 'currently_observed'
    else:
        observability_status = 'partial_or_unknown'
    classification = ('observed_sensor_alarm_requires_verification' if active else
                      'insufficient_evidence' if missing else 'no_recent_sensor_alarm_observed')
    intrusion_route = (_intrusion_route(selected=selected, observations=observations,
                                        as_of=as_of, history_source=history_source)
                       if scenario_type == 'intrusion' else None)
    return {'object_id': object_id, 'as_of': as_of.isoformat(), 'scenario_type': scenario_type,
            'title': LABELS[scenario_type], 'incident_domain': incident_domain,
            'event_scope': ('alarm_or_phase_indicator_not_confirmed_complete_outage'
                            if scenario_type == 'power_loss' else 'registered_alarm_evidence_requires_verification'),
            'classification': classification,
            'classification_kind': 'alarm_message_requires_human_verification',
            'physical_incident_confirmed': False,
            'physical_incident_probability': None,
            'generic_proxy_risk': generic_risk if generic_risk and generic_risk.get('status') == 'available' else None,
            'history_source': history_source, 'supporting_channels': active,
            'evidence': evidence, 'missing_evidence': sorted(set(missing)),
            'observability': {'status': observability_status,
                              'fresh_channel_count': len(observed),
                              'relevant_channel_count': len(selected)},
            'intrusion_route': intrusion_route,
            'planned_work': {**planned, 'source_status': planned_work.get('source_status'),
                             'unmapped_records': planned_work.get('unmapped_records', 0),
                             'causal_relationship': 'not_established_without_confirmed_work_link'},
            'equipment_terminology': CUSTOMER_EQUIPMENT_TERMINOLOGY,
            'feeder_diagnostic_options': CUSTOMER_EQUIPMENT_TERMINOLOGY['feeder_diagnostic_options'],
            'feeder_cause_diagnosed': None,
            'feeder_diagnostic_options_exhaustive': False,
            'weather': weather or {'status': 'unavailable'},
            'recommendations': recommendation,
            'decision_route': f'/objects/{object_id}',
            'human_decision_required': True}
