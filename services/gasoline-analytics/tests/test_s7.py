import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jsonschema
import pytest
import yaml
from fastapi.testclient import TestClient

from app.auth import require_reporter
from app.database import GasolineDatabase
from app.main import app
from app.models import to_iso, utc_now
from app.service import distance_m, is_stale
from shared.auth import Principal

BASE='/v1/gas-stations'
SEARCH={'latitude':43.0,'longitude':-81.2,'radius_m':1500}
COMPARE='/v1/gas-prices/comparisons'


def report(client,station='example-1',fuel='regular_87',price=1.659,key='gas-test-001',**kwargs):
    return client.post(f'{BASE}/{station}/price-reports',headers={'Idempotency-Key':key},json={
        'fuel_type':fuel,'price_per_litre':price,'currency':'CAD',**kwargs})


def test_readiness_and_request_id(client,catalog):
    response=client.get('/health',headers={'X-Request-ID':'req-123'})
    assert response.status_code==200 and response.headers['X-Request-ID']=='req-123'
    assert response.json()=={'service':'gasoline-analytics','status':'healthy'}
    assert client.get('/ready').json()['status']=='ready'
    altered=client.get('/health',headers={'X-Request-ID':'dangerous ID / text'})
    assert altered.headers['X-Request-ID']!='dangerous ID / text'
    assert client.get('/health',headers={'X-Request-ID':'dangerous ID / text'}).status_code==200


def test_empty_catalog_is_unavailable(client):
    assert client.get('/health').status_code==200
    response=client.get('/ready')
    assert response.status_code==503
    assert response.json()['error']['source']=='service'
    assert client.get(BASE,params=SEARCH).status_code==503


def test_public_search_station_detail_and_empty_result(client,catalog):
    response=client.get(BASE,params=SEARCH)
    assert response.status_code==200
    assert response.json()['pagination']['total']==3
    assert all(not item['prices'] for item in response.json()['items'])
    assert response.json()['items'][0]['price_coverage_status']=='unavailable'
    assert client.get(f'{BASE}/example-1').json()['distance_m'] is None
    assert client.get(f'{BASE}/unknown').status_code==404
    faraway=client.get(BASE,params=dict(latitude=1,longitude=1,radius_m=1000))
    assert faraway.status_code==200 and faraway.json()['items']==[]


def test_validation(client,catalog,reporter):
    bad_queries=[
        dict(radius_m=25001),dict(latitude=91),dict(radius_m=0),
        dict(min_price_per_litre=1.2),dict(fuel_type='diesel',sort_by='submitted_at',max_price_per_litre=0.12,min_price_per_litre=0.13),
        dict(fuel_type='not_a_fuel'),dict(sort_by='price_per_litre')]
    for overrides in bad_queries:
        assert client.get(BASE,params={**SEARCH,**overrides}).status_code==422
    assert client.get(BASE,params=[*SEARCH.items(),('radius_m','1500')]).status_code==422
    assert client.get(BASE,params={**SEARCH,'unknown_param':3}).status_code==422
    assert client.request('GET',BASE,content='{"not":"allowed"}').status_code==400
    assert client.get(COMPARE,params=SEARCH).status_code==422
    assert client.get(COMPARE,params={**SEARCH,'fuel_type':'regular_87','unexpected':'yes'}).status_code==422
    assert report(client,price=0).status_code==422
    assert report(client,price=-1).status_code==422
    assert report(client,price=1.6599).status_code==422
    assert report(client,price=20.001).status_code==422
    assert report(client,fuel='premium_91').status_code==422
    assert report(client,station='unknown').status_code==404
    assert report(client,observed_at='2026-11-23T08:00:00').status_code==422
    assert report(client,observed_at=None).status_code==422
    assert report(client,observed_at=to_iso(utc_now()-timedelta(days=8))).status_code==422
    assert report(client,observed_at=to_iso(utc_now()+timedelta(minutes=8))).status_code==422
    assert report(client,reporter_subject='malicious').status_code==422
    assert report(client,submitted_at=to_iso(utc_now())).status_code==422
    assert report(client,key='short').status_code==422


def test_authentication_policy(client,catalog):
    assert report(client).status_code==401
    app.dependency_overrides[require_reporter]=lambda: Principal(subject='other',roles=frozenset())
    # Do not skip permission checks in production: verify the actual role checker separately.
    from app.auth import require_reporter as check
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        check(Principal(subject='other',roles=frozenset()))
    assert error.value.status_code==403


def test_submit_and_replay(client,catalog,reporter):
    first=report(client)
    assert first.status_code==201
    body=first.json()
    assert body['station_id']=='example-1' and body['source']=='user_report'
    assert body['observed_at'] is None
    assert body['submitted_at'].endswith('Z')
    assert 'reporter_subject' not in json.dumps(body)
    replay=report(client)
    assert replay.status_code==201 and replay.json()==body
    assert report(client,price=1.699).status_code==409
    assert report(client,key='gas-test-002',price=1.699).status_code==201
    detail=client.get(BASE+'/example-1').json()
    assert detail['last_accepted_report_at'] is not None
    assert detail['prices'][0]['price_per_litre']==1.699
    assert detail['prices'][0]['is_live'] is False
    assert detail['prices'][0]['freshness_basis']=='submitted_at'
    with client.app.state.gasoline.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM price_reports').fetchone()[0]==2
        assert db.execute('SELECT COUNT(*) FROM submission_idempotency').fetchone()[0]==2


def test_comparisons_filters_and_pagination(client,catalog,reporter):
    assert report(client,price=1.659,key='report-one').status_code==201
    assert report(client,station='example-2',price=1.699,key='report-two').status_code==201
    resp=client.get(BASE,params={**SEARCH,'fuel_type':'regular_87',
                   'min_price_per_litre':'1.659','max_price_per_litre':'1.659'})
    assert resp.status_code==200
    assert [item['station_id'] for item in resp.json()['items']]==['example-1']
    filtered=client.get(BASE,params={**SEARCH,'fuel_type':'premium_91'})
    assert [item['station_id'] for item in filtered.json()['items']]==['example-2']
    comparison=client.get(COMPARE,params={**SEARCH,'fuel_type':'regular_87','limit':1})
    body=comparison.json()
    assert comparison.status_code==200
    assert body['pagination']['total']==2 and body['pagination']['has_more'] is True
    assert body['summary']['eligible_station_count']==2
    assert body['summary']['min_price_per_litre']==1.659
    assert body['summary']['max_price_per_litre']==1.699
    assert body['summary']['mean_price_per_litre']==1.679
    assert body['summary']['spread_per_litre']==0.04
    assert body['items'][0]['station_id']=='example-1'
    solo=client.get(COMPARE,params={**SEARCH,'fuel_type':'diesel'})
    assert solo.json()['summary']['comparison_status']=='insufficient_data'
    assert solo.json()['summary']['min_price_per_litre'] is None


def test_boundary_freshness_and_observation(client,catalog,reporter):
    now=utc_now()
    assert not is_stale(to_iso(now-timedelta(hours=24)),now)
    assert is_stale(to_iso(now-timedelta(hours=24,microseconds=1)),now)
    # A reported observation older than 24h is accepted but marked stale.
    observed=to_iso(now-timedelta(hours=48))
    assert report(client,observed_at=observed).status_code==201
    details=client.get(BASE+'/example-1').json()
    assert details['prices'][0]['is_stale'] is True
    assert details['prices'][0]['freshness_basis']=='observed_at'
    fresh_only=client.get(BASE+'/example-1',params={'include_stale':'false'}).json()
    assert fresh_only['prices']==[] and fresh_only['price_coverage_status']=='stale_only'
    default=client.get(COMPARE,params={**SEARCH,'fuel_type':'regular_87'}).json()
    assert default['items']==[]
    optin=client.get(COMPARE,params={**SEARCH,'fuel_type':'regular_87','include_stale':'true'}).json()
    assert len(optin['items'])==1 and optin['items'][0]['quote']['is_stale'] is True
    # Timestamp with a numeric offset is normalized to Z, not replaced.
    offset=(now-timedelta(minutes=3)).astimezone(timezone(timedelta(hours=-5)))
    report2=report(client,station='example-2',observed_at=offset.isoformat(),key='valid-offset')
    assert report2.status_code==201
    assert report2.json()['observed_at'].endswith('Z')


def test_restarts_preserve_reports_and_idempotency(client,catalog,reporter):
    assert report(client).status_code==201
    path=client.app.state.gasoline.path
    another_db=GasolineDatabase(path)
    assert another_db.ready()
    assert another_db.get_station_rows(station_id='example-1')[0]['price_report_id'] is not None
    with another_db.connect() as db:
        assert db.execute('SELECT count(*) FROM submission_idempotency').fetchone()[0]==1


def test_import_atomicity(client,tmp_path):
    from app.catalog_import import import_csv
    p=tmp_path/'stations.csv'
    p.write_text('station_id,name,address,latitude,longitude,available_fuel_types\n'
                 'valid,Valid Station,1 Road,43,-81,regular_87|diesel\n'
                 'invalid,Bad Station,2 Road,400,-81,regular_87\n')
    with pytest.raises(ValueError):
        import_csv(client.app.state.gasoline,p,'test-data')
    assert not client.app.state.gasoline.ready()
    p.write_text('station_id,name,address,latitude,longitude,available_fuel_types\n'
                 'valid,Valid Station,1 Road,43,-81,regular_87|diesel\n')
    assert import_csv(client.app.state.gasoline,p,'test-data')==1
    assert client.app.state.gasoline.ready()


def test_schema_against_approved_openapi(client,catalog,reporter):
    spec_file=Path(__file__).resolve().parents[3]/'shared/api-contracts/s7-gasoline-analytics.openapi.yaml'
    spec=yaml.safe_load(spec_file.read_text())
    checks=[('HealthResponse',client.get('/health')),
            ('ReadyResponse',client.get('/ready')),
            ('StationSearchResponse',client.get(BASE,params=SEARCH)),
            ('StationDetail',client.get(BASE+'/example-1')),
            ('ComparisonResponse',client.get(COMPARE,params={**SEARCH,'fuel_type':'regular_87'})),
            ('PriceReportResponse',report(client))]
    for schema_name,response in checks:
        assert response.status_code in (200,201),response.text
        validator=jsonschema.Draft202012Validator({'$ref':f'#/components/schemas/{schema_name}',
                                                  'components':spec['components']})
        validator.validate(response.json())
    response=client.get(BASE+'/unknown')
    jsonschema.Draft202012Validator({'$ref':'#/components/schemas/ErrorEnvelope',
                                     'components':spec['components']}).validate(response.json())
    assert set(p for p in spec['paths'])=={p.path for p in app.routes if p.path in spec['paths']}


def test_bounded_search_speed(client,catalog):
    # Local sanity test only, NOT a substantiated 1,500 ms production SLA.
    durations=[]
    for _ in range(20):
        start=time.perf_counter()
        assert client.get(BASE,params=SEARCH).status_code==200
        durations.append((time.perf_counter()-start)*1000)
    assert max(durations)<1500


def test_geo_radius_inclusive_and_antimeridian(client):
    db=client.app.state.gasoline
    # At the equator 1 degree longitude is about 111,195m with our spherical model.
    edge_lon=1000 / 6371008.8 * 180 / 3.141592653589793
    db.import_stations([
        dict(station_id='boundary',name='Boundary',address='Synthetic',latitude=0.0,
             longitude=edge_lon,available_fuel_types=['regular_87']),
        dict(station_id='across-date-line',name='Date Line',address='Synthetic',latitude=0.0,
             longitude=-179.999,available_fuel_types=['regular_87'])],
        'synthetic_test_fixture',to_iso(utc_now()))
    near_boundary=client.get(BASE,params={'latitude':0,'longitude':0,'radius_m':1000}).json()
    assert [r['station_id'] for r in near_boundary['items']]==['boundary']
    across_line=client.get(BASE,params={'latitude':0,'longitude':179.999,'radius_m':1000}).json()
    assert [r['station_id'] for r in across_line['items']]==['across-date-line']


def test_price_type_and_real_restart_replay(client,catalog,reporter):
    # The price field's JSON type must be numeric, per the approved OpenAPI.
    assert report(client,price='1.659').status_code==422
    first=report(client).json()
    prior_path=client.app.state.gasoline.path
    # New database instance simulates service restart, using unchanged durable files.
    client.app.state.gasoline=GasolineDatabase(prior_path)
    replay=report(client)
    assert replay.status_code==201 and replay.json()==first
    compare=client.get(COMPARE,params={**SEARCH,'fuel_type':'regular_87'})
    assert compare.status_code==200
    assert compare.json()['items'][0]['quote']['price_report_id']==first['price_report_id']
