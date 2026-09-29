from datetime import datetime, timedelta, timezone
import json

import pytest

from backend.app.services.auth import AuthUser
from backend.app.services.weather_context import weather_context_at
from scripts.fetch_public_weather import fetch_public_forecast


def test_cumulative_roles_do_not_grant_admin_dispatcher_access():
    user = AuthUser('user','user','system_administrator',tuple(),('system_administrator',),(),())
    assert user.has_capability('access.manage')
    assert not user.has_capability('signals.decide')
    assert not user.has_role('central_dispatcher')
    combined = AuthUser('both','both','district_dispatcher',(9,),
                        ('district_dispatcher','technician'),(),())
    assert combined.has_capability('signals.decide','tasks.execute')
    assert combined.scope_roots == (9,)


def test_public_moscow_weather_fetch_does_not_fabricate_observation(monkeypatch):
    import scripts.fetch_public_weather as weather
    now = datetime(2026,9,28,9,tzinfo=timezone.utc)
    payload = {'hourly': {'time':['2026-09-28T08:00','2026-09-28T09:00','2026-09-28T12:00'],
                           'temperature_2m':[10,11,13], 'relative_humidity_2m':[70,66,60],
                           'precipitation':[1,0,2], 'pressure_msl':[1005,1004,1006]}}
    class FakeResponse:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self,*args):return json.dumps(payload).encode()
    monkeypatch.setattr(weather,'urlopen',lambda *_args,**_kwargs:FakeResponse())
    result=fetch_public_forecast(now=now)
    assert len(result)==3 and all(row['kind']=='forecast' for row in result)
    assert all(row['issued_at']==now.isoformat() for row in result)
    assert result[0]['relative_humidity_percent']==70 and result[0]['pressure_hpa']==1005
    historical=weather_context_at(result,now-timedelta(minutes=1))
    assert not historical['observations'] and not historical['forecasts']
    current=weather_context_at(result,now)
    assert current['status']=='available' and current['object_coordinates_used'] is False


def test_ldap_filter_template_and_insecure_remote_are_rejected_before_network():
    from backend.app.services.ldap_auth import LDAPAuthProvider,LDAPConfigurationError
    with pytest.raises(LDAPConfigurationError, match='filter'):
        LDAPAuthProvider(url='ldaps://localhost',base_dn='dc=example,dc=org',group_map={},
                         user_filter='(uid={username})({username})',token_secret='z'*64)
    with pytest.raises(LDAPConfigurationError, match='localhost'):
        LDAPAuthProvider(url='ldap://example.org',base_dn='dc=example,dc=org',group_map={},
                         insecure_local_test=True,token_secret='z'*64)
