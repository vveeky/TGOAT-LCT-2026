from datetime import datetime, timedelta, timezone

from backend.app.services.incident_scenarios import analyze_scenario, relevant_channel

NOW = datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)
CHANNELS = [{'channel_id': 1, 'sensor_type': 'Датчик дыма', 'sensor_name': 'Дым', 'system_type': 'Пожарная охрана'},
            {'channel_id': 2, 'sensor_type': 'КД Дверь', 'sensor_name': 'Дверь', 'system_type': 'Охранная подсистема'},
            {'channel_id': 3, 'sensor_type': 'Датчик затопления', 'sensor_name': 'Вода', 'system_type': 'Диспетчерский контроль'},
            {'channel_id': 4, 'sensor_type': 'Газовый датчик', 'sensor_name': 'Газ', 'system_type': 'Газовая охрана'},
            {'channel_id': 5, 'sensor_type': 'Датчик температуры', 'sensor_name': 'Температура', 'system_type': 'Температурная подсистема'},
            {'channel_id': 6, 'sensor_type': 'Состояние фазы', 'sensor_name': 'Фаза', 'system_type': 'Контроль электроснабжения'},
            {'channel_id': 7, 'sensor_type': 'Состояние связи', 'sensor_name': 'Связь', 'system_type': 'Канал связи'}]
PLANNED = {'active': [], 'recent': [], 'upcoming': [], 'source_status': 'available', 'unmapped_records': 613}


def run(kind, events=(), planned_work=PLANNED, **other):
    return analyze_scenario(scenario_type=kind, object_id=99, as_of=NOW, channels=CHANNELS,
                            observations=events, history_source='historical_replay',
                            planned_work=planned_work, **other)


def test_scenario_taxonomy_no_cross_system_leakage():
    assert relevant_channel('fire', 'Датчик дыма')
    assert not relevant_channel('fire', 'КД Дверь')
    assert relevant_channel('intrusion', 'КД Дверь')
    assert relevant_channel('flooding', 'Датчик затопления')
    assert {x['channel_id'] for x in run('fire')['evidence']} == {1}
    assert {x['channel_id'] for x in run('intrusion')['evidence']} == {2}
    assert {x['channel_id'] for x in run('flooding')['evidence']} == {3}


def test_future_alarm_is_never_accepted_even_if_supplier_leaks_future_row():
    result = run('fire', [{'channel_id': 1, 'timestamp': NOW + timedelta(minutes=1), 'alarm': True}])
    assert not result['supporting_channels']
    assert result['evidence'][0]['state'] == 'no_observation_in_window'


def test_real_registered_alarm_is_evidence_not_physical_incident_probability():
    result = run('fire', [{'channel_id': 1, 'timestamp': NOW - timedelta(minutes=5), 'alarm': True,
                           'value': 'Обнаружен дым'}], generic_risk={'status': 'available', 'probability': .7, 'kind': 'generic_proxy_alarm_24h'})
    assert result['classification'] == 'observed_sensor_alarm_requires_verification'
    assert result['supporting_channels'] == [1]
    assert result['physical_incident_probability'] is None
    assert result['generic_proxy_risk']['kind'] == 'generic_proxy_alarm_24h'
    assert result['human_decision_required']


def test_stale_or_missing_source_not_interpreted_as_normal():
    result = run('intrusion', [{'channel_id': 2, 'timestamp': NOW - timedelta(days=1), 'alarm': False}])
    assert result['classification'] == 'insufficient_evidence'
    assert result['evidence'][0]['state'] == 'stale'
    assert any('непривязанные' in text for text in result['missing_evidence'])


def test_flooding_missing_weather_pump_is_explicit():
    result = run('flooding')
    assert any('Метеоконтекст' in x for x in result['missing_evidence'])
    assert any('насоса' in x for x in result['missing_evidence'])
    assert any(x['code'] == 'verify_weather_pumps' for x in result['recommendations'])


def test_weather_issued_later_cannot_leak_into_historical_view():
    from backend.app.services.weather_context import weather_context_at
    history = weather_context_at([
        {'kind': 'observation', 'issued_at': NOW-timedelta(hours=1), 'valid_at': NOW+timedelta(minutes=1), 'precipitation_mm': 2},
        {'kind': 'forecast', 'issued_at': NOW+timedelta(minutes=1), 'valid_at': NOW+timedelta(hours=3), 'precipitation_mm': 9},
        {'kind': 'forecast', 'issued_at': NOW-timedelta(hours=1), 'valid_at': NOW+timedelta(hours=3), 'precipitation_mm': 1},
    ], NOW)
    assert len(history['forecasts']) == 1
    assert not history['observations']
    assert history['forecasts'][0]['precipitation_mm'] == 1
    assert history['object_coordinates_used'] is False


def test_alarm_message_requires_human_verification_not_incident_or_dispatch():
    result = run('fire', [{'channel_id': 1, 'timestamp': NOW, 'alarm': True}])
    assert result['classification'] == 'observed_sensor_alarm_requires_verification'
    assert result['physical_incident_confirmed'] is False
    assert result['physical_incident_probability'] is None
    assert result['human_decision_required'] is True
    assert 'ремонтной бригады' in result['recommendations'][0]['detail']


def test_gas_alarm_is_included_and_not_dropped_by_scenario_taxonomy():
    result = run('gas', [{'channel_id': 4, 'timestamp': NOW, 'alarm': True, 'value': 'alarm'}])
    assert [row['channel_id'] for row in result['evidence']] == [4]
    assert result['supporting_channels'] == [4]
    assert result['incident_domain'] == 'life_safety'


def test_security_alarm_is_a_verification_signal():
    result = run('intrusion', [{'channel_id': 2, 'timestamp': NOW, 'alarm': True}])
    assert result['supporting_channels'] == [2]
    assert result['human_decision_required'] is True
    assert result['physical_incident_confirmed'] is False


def test_missing_observation_is_unknown_not_no_incident():
    result = run('fire')
    assert result['classification'] == 'insufficient_evidence'
    assert result['observability']['status'] == 'loss_indicated'
    assert result['physical_incident_confirmed'] is False


def test_power_loss_is_separate_from_fire_and_is_not_diagnosed_from_alarm():
    power = run('power_loss', [{'channel_id': 6, 'timestamp': NOW, 'alarm': True}])
    fire = run('fire', [{'channel_id': 6, 'timestamp': NOW, 'alarm': True}])
    assert power['incident_domain'] == 'operational_disruption'
    assert power['supporting_channels'] == [6]
    assert power['feeder_cause_diagnosed'] is None
    assert fire['supporting_channels'] == []


def test_anomalous_temperature_is_not_automatically_fire():
    temperature = run('anomalous_temperature', [{'channel_id': 5, 'timestamp': NOW, 'alarm': True}])
    fire = run('fire', [{'channel_id': 5, 'timestamp': NOW, 'alarm': True}])
    assert temperature['supporting_channels'] == [5]
    assert fire['supporting_channels'] == []


def test_repeated_alarm_does_not_establish_ppr_without_linked_planned_work():
    result = run('gas', [
        {'channel_id': 4, 'timestamp': NOW - timedelta(minutes=4), 'alarm': True},
        {'channel_id': 4, 'timestamp': NOW - timedelta(minutes=2), 'alarm': True},
    ], planned_work={'active': [{'id': 'work'}], 'recent': [], 'upcoming': [],
                     'source_status': 'available', 'unmapped_records': 0})
    assert result['evidence'][0]['sampled_alarms'] == 2
    assert result['planned_work']['causal_relationship'] == 'not_established_without_confirmed_work_link'
    assert any('не доказывает проведение ППР' in item['detail'] for item in result['recommendations'])


def test_intrusion_route_is_chronological_as_of_bounded_and_has_no_invented_topology():
    future = NOW + timedelta(seconds=1)
    result = run('intrusion', [
        {'channel_id': 2, 'timestamp': NOW - timedelta(minutes=1), 'alarm': True},
        {'channel_id': 2, 'timestamp': future, 'alarm': True},
    ])
    route = result['intrusion_route']
    assert len(route['points']) == 1
    assert route['points'][0]['timestamp'] == (NOW - timedelta(minutes=1)).isoformat()
    assert route['route_geometry'] is None
    assert route['sequence_completeness'] == 'complete_within_selected_replay_window'
    assert 'топология' in route['limitation'].casefold()


def test_route_from_latest_only_history_is_explicitly_incomplete():
    result = analyze_scenario(scenario_type='intrusion', object_id=99, as_of=NOW,
                              channels=CHANNELS, observations=[
                                  {'channel_id': 2, 'timestamp': NOW, 'alarm': True}],
                              history_source='annual_event_history_latest_only', planned_work=PLANNED)
    assert result['intrusion_route']['sequence_completeness'] == 'incomplete_latest_state_per_channel_only'


def test_customer_equipment_terms_do_not_invent_upstream_or_fixed_picket_spacing():
    from backend.app.services.equipment_reference import CUSTOMER_EQUIPMENT_TERMINOLOGY as terms
    assert terms['abbreviations']['ФВ'] == 'фидер вентиляции'
    assert terms['designation_rules'][2].startswith('Источник питания самого фидера не указан')
    assert terms['picket_spacing']['typical_meters'] == 10
    assert terms['picket_spacing']['is_universal'] is False
    assert terms['feeder_diagnostic_options_exhaustive'] is False
    assert run('fire')['feeder_cause_diagnosed'] is None


def test_communication_loss_does_not_claim_physical_incident_absent():
    result = run('communication_loss')
    assert result['incident_domain'] == 'operational_disruption'
    assert result['classification'] == 'insufficient_evidence'
    assert result['physical_incident_confirmed'] is False
    assert result['observability']['status'] == 'loss_indicated'
