"""Exercise the installed native plugin, profile isolation, and verifier I/O contracts."""
from pathlib import Path
import argparse
import json
import shutil
import sys

import pytest
import hermes_yaml as yaml
from hermes_cli.plugins import PluginManager
from hermes_constants import set_hermes_home_override, reset_hermes_home_override
from tools.registry import registry

PLUGIN = Path(__file__).resolve().parents[1]


@pytest.fixture
def installed(tmp_path, monkeypatch):
    home = tmp_path / 'profile-a'
    plugin = home / 'plugins' / 'bend'
    shutil.copytree(PLUGIN, plugin, ignore=shutil.ignore_patterns('.git', '__pycache__', '.pytest_cache'))
    (home / 'config.yaml').write_text(yaml.safe_dump({'plugins': {'enabled': ['bend']}}))
    bundled = tmp_path / 'bundled'
    bundled.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HERMES_BUNDLED_PLUGINS', str(bundled))
    manager = PluginManager()
    manager.discover_and_load()
    loaded = manager._plugins['bend']
    assert loaded.enabled and loaded.error is None
    yield manager, loaded.module, home
    manager.unload('bend')


def test_native_tool_cli_skill_and_profile_config(installed, tmp_path, capsys):
    manager, module, home = installed
    assert 'bend:workflow' in manager._plugin_skills
    entry = registry.get_entry('bend_verify')
    assert entry is not None
    result = json.loads(registry.dispatch('bend_verify', {'project_dir': 'relative'}))
    assert result['code'] == 'invalid_input' and result['success'] is False
    # The same loaded service must read each active profile's settings, A -> B -> A.
    service = entry.handler.__self__
    alternate = tmp_path / 'profile-b'
    alternate.mkdir()
    for profile, command in ((home, '/missing/profile-a-bend'), (alternate, '/missing/profile-b-bend')):
        (profile / 'config.yaml').write_text(yaml.safe_dump({'plugins': {
            'enabled': ['bend'], 'entries': {'bend': {'settings': {'executable': command}}}}}))
    for profile in (home, alternate, home):
        token = set_hermes_home_override(profile)
        try:
            assert service.ctx.get_config('executable').endswith(profile.name + '-bend')
            parser = argparse.ArgumentParser()
            command = manager._cli_commands['bend']
            command['setup_fn'](parser)
            with pytest.raises(SystemExit) as stopped:
                command['handler_fn'](parser.parse_args(['doctor']))
            assert stopped.value.code == 2
            assert json.loads(capsys.readouterr().out)['code'] == 'bend_not_found'
            service.save_receipt({'receipt_id': profile.name})
            assert service.ctx.state.get('last_receipt')['receipt_id'] == profile.name
        finally:
            reset_hermes_home_override(token)
    manager.unload('bend')
    assert registry.get_entry('bend_verify') is None
    assert 'bend' not in manager._cli_commands


def test_snapshot_verdict_and_subprocess_contract(installed, tmp_path):
    _, module, _ = installed
    # Load through the package produced by real Hermes discovery.
    import importlib
    verifier = importlib.import_module(module.__name__ + '.verify_core')
    project = tmp_path / 'project'
    shutil.copytree(PLUGIN / 'examples/basic', project)
    installation = tmp_path / 'installation'
    (installation / 'bin').mkdir(parents=True)
    (installation / 'bend2').mkdir()
    bend = installation / 'bin/bend'
    bend.write_text('fixture binary identity')
    for name in ('base.bend', 'bendtt.lean'):
        (installation / 'bend2' / name).write_text('fixture runtime identity')
    import subprocess
    def run(argv, **kwargs):
        if argv[-1] == 'version':
            return subprocess.CompletedProcess(argv, 0, 'bend 2.0.34\n', '')
        assert (Path(kwargs['cwd']) / 'PROOF.bend').read_bytes() == (project / 'PROOF.bend').read_bytes()
        assert 'UNRELATED_CREDENTIAL' not in kwargs['env']
        return subprocess.CompletedProcess(argv, 0, 'ALL PROOFS CHECK\n', '')
    receipt = verifier.verify(str(project), which=lambda _: str(bend), runner=run,
                              source_env={'UNRELATED_CREDENTIAL': 'test-value'})
    assert receipt['success'] and not receipt['source_semantics_attested']
    assert receipt['verification_scope'] == 'bend-emitted-book'
    first = receipt['input_manifest_sha256']
    (project / 'LAWS.bend').write_text('import Base\n# changed specification\n')
    assert verifier.capture_local_inputs(project, project / 'PROOF.bend')['manifest_sha256'] != first
    (project / 'PROOF.bend').write_text('import sample@1.0.0.0/Main.bend as S\n')
    with pytest.raises(verifier.BendVerifyError, match='Hub'):
        verifier.capture_local_inputs(project, project / 'PROOF.bend')
    assert verifier.run_process([sys.executable, '-c', 'print("hello")'], timeout=5).stdout == 'hello\n'
    with pytest.raises(subprocess.TimeoutExpired):
        verifier.run_process([sys.executable, '-c', 'import time; time.sleep(20)'], timeout=0.1)
    with pytest.raises(verifier.BendVerifyError, match='output exceeded'):
        verifier.run_process([sys.executable, '-c', 'print("x" * (2 * 1024 * 1024))'], timeout=5)


def test_cached_hub_closure_is_content_addressed_and_replay_invalidates(installed, tmp_path):
    _, module, _ = installed
    import importlib
    verifier = importlib.import_module(module.__name__ + '.verify_core')
    receipts = importlib.import_module(module.__name__ + '.receipts')
    cache = tmp_path / 'cache'
    helper = b'import Base\n\ndef zero() -> Nat:\n  0n\n'
    digest = verifier.sha256_bytes((verifier.sha256_bytes(helper) + ' Main.bend\n').encode())
    package = '0x' + digest[:32]
    (cache / package).mkdir(parents=True)
    (cache / package / 'Main.bend').write_bytes(helper)
    (cache / 'names').mkdir()
    (cache / 'names' / 'sample@1.0.0.0').write_text(package + '\n')
    project = tmp_path / 'project'
    project.mkdir()
    proof = project / 'PROOF.bend'
    proof.write_text('import sample@1.0.0.0/Main.bend as S\n')
    captured = verifier.capture_local_inputs(project, proof, str(cache))
    assert captured['dependencies']['packages'] == {package: digest}
    assert captured['dependencies']['names'] == {'sample@1.0.0.0': package}
    assert captured['dependencies']['files'][package + '/Main.bend'] == helper
    old = {'proof_file': 'PROOF.bend', 'input_manifest_sha256': captured['manifest_sha256']}
    service = registry.get_entry('bend_verify').handler.__self__
    service.ctx.set_config('dependency_cache', str(cache))
    proof.write_text('import Base\n')
    with pytest.raises(verifier.BendVerifyError) as exc:
        receipts.replay(service, old, str(project))
    assert exc.value.code == 'receipt_stale'
    proof.write_text('import sample@1.0.0.0/Main.bend as S\n')
    (cache / package / 'Main.bend').write_text('import Base\n# edited cache\n')
    with pytest.raises(verifier.BendVerifyError) as exc:
        verifier.capture_local_inputs(project, proof, str(cache))
    assert exc.value.code == 'dependency_integrity'


def test_z0_stack_installed_hooks_state_and_profiles(installed, tmp_path, monkeypatch, capsys):
    import subprocess
    import time
    manager, module, home = installed
    command = manager._cli_commands['z0']
    stack = command['handler_fn'].__closure__[0].cell_contents
    assert 'bend:z0-stack' in manager._plugin_skills
    assert manager.invoke_hook('pre_api_request', session_id='s', turn_id='t', api_request_id='a') == []
    assert not stack.home().exists()  # default off
    repo = tmp_path / 'project'
    repo.mkdir()
    subprocess.run(['git', 'init', str(repo)], check=True, capture_output=True)
    (repo / 'README.md').write_text('# Current work\n\n- [ ] Integrate the stack\n')
    monkeypatch.chdir(repo)
    (home / 'config.yaml').write_text(yaml.safe_dump({'plugins': {'enabled': ['bend'], 'entries': {
        'bend': {'settings': {'stack_mode': 'shadow', 'stack_opportunities': True}}}}}))
    monkeypatch.setenv('Z0INT_HERMES_CAPTURE_SANITIZED_CONTENT', '1')
    payload = dict(session_id='s', turn_id='t', api_request_id='a', api_call_count=1, platform='cli')
    assert manager.invoke_hook('pre_llm_call', **payload, user_message='What is the current branch?') == []
    assert manager.invoke_hook('pre_api_request', **payload, request={'secret': 'private-canary'}) == []
    assert manager.invoke_hook('api_request_error', **payload, reason='timeout') == []
    assert manager.invoke_hook('post_llm_call', **payload, conversation_history=[
        {'role': 'user', 'content': 'private-canary'}, {'role': 'assistant', 'content': 'Done'}]) == []
    assert manager.invoke_hook('post_tool_call', **payload, tool_name='bend_verify', result=json.dumps({
        'receipt_id': 'proof-artifact', 'success': True, 'verification_scope': 'bend-emitted-book',
        'source_semantics_attested': False, 'input_manifest_sha256': 'abc'})) == []
    deadline = time.monotonic() + 15
    while stack.pending.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.02)
    assert stack.pending.unfinished_tasks == 0 and stack.error is None
    report = stack.report()
    assert report['events'] == 5
    assert len(report['examples']) == 1 and report['examples'][0]['verified_outcome'] is True
    assert report['verified_task_success'] is None
    text = (stack.home() / 'events.jsonl').read_text()
    assert 'private-canary' not in text
    proof_row = json.loads(text.splitlines()[-1])
    assert proof_row['bend_evidence']['receipt_id'] == 'proof-artifact'
    assert proof_row['label_kind'] == 'scoped_proof_evidence_not_task_success'
    record = json.loads((stack.home() / 'opportunities.jsonl').read_text())
    assert record['opportunity']['trace']['trace_id'] == report['examples'][0]['identity']['trace_id']
    assert record['opportunity']['authority']['grants'] == ['read']
    assert record['opportunity']['expected_outcome']['verifier'] is None
    first = stack.project(repo)['packet']
    (repo / 'README.md').write_text('# Changed current work\n')
    second = stack.project(repo)['packet']
    assert first['packet_id'] != second['packet_id']
    alternate = tmp_path / 'profile-b'
    alternate.mkdir()
    (alternate / 'config.yaml').write_text(yaml.safe_dump({'plugins': {'enabled': ['bend'], 'entries': {
        'bend': {'settings': {'stack_mode': 'shadow'}}}}}))
    token = set_hermes_home_override(alternate)
    try:
        assert manager.invoke_hook('on_session_start', session_id='other') == []
        stack.project(repo)
    finally:
        reset_hermes_home_override(token)
    stack.close()
    assert (alternate / 'plugin-data/bend/z0/events.jsonl').exists()
    assert stack.home() == home / 'plugin-data/bend/z0'
    assert 'other' not in (stack.home() / 'events.jsonl').read_text()
    parser = argparse.ArgumentParser()
    command['setup_fn'](parser)
    with pytest.raises(SystemExit) as stopped:
        command['handler_fn'](parser.parse_args(['status']))
    assert stopped.value.code == 0
    assert json.loads(capsys.readouterr().out)['model_runtime_loaded'] is False


def test_z0_bundled_source_integrity_and_evaluation(installed):
    import hashlib
    import importlib
    _, module, _ = installed
    root = Path(module.__file__).parent
    sources = json.loads((root / 'stack/sources.json').read_text())
    for source in sources['files']:
        assert hashlib.sha256((root / source['bundled']).read_bytes()).hexdigest() == source['sha256']
    evaluate = importlib.import_module(module.__name__ + '.stack.observer.evaluate_shadow').evaluate
    result = evaluate([{'verified_outcome': True, 'decision': {'answers': [
        {'question_id': 'api.attempt_will_fail', 'probabilities': {'true': 0.8}}]}}])
    assert result['n'] == 1 and result['promotion_ready'] is False
    assert result['brier'] == pytest.approx(0.04)


def test_z0_runtime_uses_exact_contract_without_retries(installed):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.error import HTTPError
    _, module, _ = installed
    import importlib
    Stack = importlib.import_module(module.__name__ + '.stack.bridge').Stack
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b'{"error":"busy"}')

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ctx = type('Context', (), {'get_config': lambda self, key, default: server.server_port})()
    stack = Stack(ctx)
    request = {'harness': 'hermes', 'trace_id': 'stable-identity', 'allow_remote': False}
    try:
        with pytest.raises(HTTPError):
            stack.runtime('worker', request)
        assert requests == [('/v1/worker', request)]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
