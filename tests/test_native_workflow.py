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
