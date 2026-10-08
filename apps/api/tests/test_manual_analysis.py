from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from uuid import uuid4

import pytest

from apps.api.app.dependencies import get_manual_analysis_coordinator, get_analysis_service
from apps.api.app.main import app
from apps.api.app.manual_analysis import ManualAnalysisCoordinator
from apps.api.app.routes.analyses import settings
from apps.api.tests.conftest import VALID_GEOMETRY, make_result
from apps.api.tests.test_automatic_analysis import HoldingExecutor, _coordinator, BOUNDS, CENTER
from apps.api.app.dependencies import get_automatic_analysis_coordinator


@pytest.fixture
def jobs(client, tmp_path):
    executor = HoldingExecutor()
    coordinator = ManualAnalysisCoordinator(tmp_path / 'jobs.sqlite3', executor)
    app.dependency_overrides[get_manual_analysis_coordinator] = lambda: coordinator
    return coordinator, executor


@pytest.mark.parametrize('terminal', ['completed', 'insufficient_observations'])
def test_manual_creation_polling_terminal_and_history(client, jobs, terminal):
    coordinator, executor = jobs
    calls = []
    def service(config, *, analysis_id):
        calls.append(config)
        result = make_result(analysis_id)
        result.status = terminal
        if terminal == 'insufficient_observations':
            result.recommendation['recommendation'] = 'inconclusivo'
        return result
    app.dependency_overrides[get_analysis_service] = lambda: service
    created = client.post('/api/analyses/jobs', json={'geometry': VALID_GEOMETRY})
    assert created.status_code == 202
    analysis_id = created.json()['analysis_id']
    assert calls == []  # Sentinel work has not run in the original HTTP request.
    assert client.get(f'/api/analyses/jobs/{analysis_id}').json()['status'] == 'in_progress'
    function, args = executor.calls.pop()
    function(*args)
    status = client.get(f'/api/analyses/jobs/{analysis_id}').json()
    assert status['status'] == 'completed'
    assert status['result']['status'] == terminal
    assert status['result']['analysis_trigger'] == 'manual'
    assert status['result']['analysis_id'] == analysis_id
    if terminal == 'insufficient_observations':
        assert status['result']['recommendation']['decision'] == 'inconclusivo'
    history = client.get(f'/api/analyses/{analysis_id}')
    assert history.status_code == 200
    assert history.json()['result'] == status['result']
    other = {'X-Operator-Scope': str(uuid4())}
    assert client.get(f'/api/analyses/jobs/{analysis_id}', headers=other).status_code == 404
    assert client.get(f'/api/analyses/{analysis_id}', headers=other).status_code == 404
    assert len(calls) == 1


@pytest.mark.parametrize('failure', ['failed', 'exception', 'persistence'])
def test_manual_failed_stays_error(client, jobs, monkeypatch, failure):
    def service(config, *, analysis_id):
        if failure == 'exception':
            raise RuntimeError('private details')
        result = make_result(analysis_id)
        if failure == 'failed':
            result.status = 'failed'
            result.errors = [{'code': 'SATELLITE_PROVIDER_ERROR'}]
        return result
    if failure == 'persistence':
        def fail(*args, **kwargs):
            raise RuntimeError('database unavailable')
        monkeypatch.setattr('apps.api.app.routes.analyses.save_analysis', fail)
    app.dependency_overrides[get_analysis_service] = lambda: service
    created = client.post('/api/analyses/jobs', json={'geometry': VALID_GEOMETRY}).json()
    function, args = jobs[1].calls.pop()
    function(*args)
    status = client.get(f"/api/analyses/jobs/{created['analysis_id']}").json()
    assert status['status'] == 'failed'
    assert status['result'] is None
    assert status['error_code'] == ('SATELLITE_PROVIDER_ERROR' if failure == 'failed' else 'PROCESSING_ERROR')
    assert 'private details' not in str(status)


def test_real_background_worker_does_not_block_creation(tmp_path):
    entered, release = Event(), Event()
    def execute(analysis_id):
        entered.set()
        assert release.wait(5)
        return {'status': 'insufficient_observations'}
    with ThreadPoolExecutor(max_workers=1) as executor:
        coordinator = ManualAnalysisCoordinator(tmp_path / 'jobs.sqlite3', executor)
        try:
            job = coordinator.start('group', execute)
            assert entered.wait(5)
            assert coordinator.get(job['analysis_id'], 'group')['status'] == 'in_progress'
        finally:
            release.set()
    assert coordinator.get(job['analysis_id'], 'group')['result']['status'] == 'insufficient_observations'


def test_manual_timeout_and_capacity(client, jobs):
    first = client.post('/api/analyses/jobs', json={'geometry': VALID_GEOMETRY}).json()
    assert client.post('/api/analyses/jobs', json={'geometry': VALID_GEOMETRY}).status_code == 202
    assert client.post('/api/analyses/jobs', json={'geometry': VALID_GEOMETRY}).status_code == 429
    with jobs[0]._connect() as db:
        db.execute("UPDATE manual_analysis_jobs SET deadline='2000-01-01' WHERE analysis_id=?", (first['analysis_id'],))
    status = client.get(f"/api/analyses/jobs/{first['analysis_id']}").json()
    assert status['status'] == 'failed'
    assert status['error_code'] == 'JOB_TIMEOUT'


@pytest.mark.parametrize('lookback, expected', [(None, '2026-07-10'), (60, '2026-06-11')])
def test_manual_automatic_share_period_and_new_cache_key(client, jobs, tmp_path, monkeypatch, lookback, expected):
    monkeypatch.setattr('apps.api.app.routes.analyses.settings', replace(settings, analysis_lookback_days=lookback))
    automatic = _coordinator(tmp_path)
    app.dependency_overrides[get_automatic_analysis_coordinator] = lambda: automatic
    configs = []
    def service(config, *, analysis_id):
        configs.append(config)
        return make_result(analysis_id)
    app.dependency_overrides[get_analysis_service] = lambda: service
    client.post('/api/analyses/jobs', json={'geometry': VALID_GEOMETRY})
    function, args = jobs[1].calls.pop()
    function(*args)
    payload = {'bounds': BOUNDS, 'center': CENTER, 'zoom': 17}
    first = client.post('/api/analyses/automatic', json=payload).json()
    cached = client.post('/api/analyses/automatic', json=payload).json()
    assert cached['status'] == 'cache_hit'
    assert len(configs) == 2
    assert all(config.start_date.isoformat() == expected for config in configs)
    assert all(config.end_date.isoformat() == '2026-08-10' for config in configs)
    assert all(config.min_observations == config.decision_min_observations == 4 for config in configs)
    monkeypatch.setattr('apps.api.app.routes.analyses.settings', replace(settings, analysis_lookback_days=90))
    changed = client.post('/api/analyses/automatic', json=payload).json()
    assert changed['status'] == 'analysis_started'
    assert changed['analysis_id'] != first['analysis_id']
    assert len(configs) == 3
