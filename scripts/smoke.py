#!/usr/bin/env python3
"""Real native-plugin smoke; never downloads tools or uses a user's Hermes home."""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--hermes-root', type=Path, required=True)
    parser.add_argument('--bend', type=Path, required=True)
    parser.add_argument('--lean-bin', type=Path, required=True)
    parser.add_argument('--aodl-project', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.hermes_root.resolve()))
    from hermes_cli.plugins import PluginManager
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from tools.registry import registry
    import hermes_yaml as yaml

    plugin = Path(__file__).resolve().parents[1]
    os.environ['PATH'] = str(args.lean_bin.resolve()) + os.pathsep + os.environ['PATH']
    records = {}
    with tempfile.TemporaryDirectory(prefix='bend-native-smoke-') as scratch:
        root = Path(scratch)
        homes = [root / 'profile-a', root / 'profile-b']
        for home in homes:
            shutil.copytree(plugin, home / 'plugins/bend', ignore=shutil.ignore_patterns('.git', '__pycache__', '.pytest_cache'))
            (home / 'config.yaml').write_text(yaml.safe_dump({'plugins': {'enabled': ['bend'],
                'entries': {'bend': {'settings': {'executable': str(args.bend.resolve()),
                                                 'dependency_cache': str(root / 'hub-cache')}}}}}))
        (root / 'bundled').mkdir()
        os.environ['HERMES_HOME'] = str(homes[0])
        os.environ['HERMES_BUNDLED_PLUGINS'] = str(root / 'bundled')
        project = root / 'project'
        shutil.copytree(plugin / 'examples/basic', project)
        managers = []
        try:
            for index, home in enumerate(homes):
                token = set_hermes_home_override(home)
                try:
                    manager = PluginManager()
                    managers.append(manager)
                    manager.discover_and_load()
                    assert manager._plugins['bend'].error is None
                    module = manager._plugins['bend'].module
                    service = registry.get_entry('bend_verify').handler.__self__
                    receipt = json.loads(registry.dispatch('bend_verify', {'project_dir': str(project)}))
                    assert receipt['success'], receipt
                    assert receipt['kernel_sha256_after'] and not receipt['source_semantics_attested']
                    records[home.name] = receipt
                    assert service.ctx.state.get('last_receipt')['receipt_id'] == receipt['receipt_id']
                    if index == 0:
                        io = importlib.import_module(module.__name__ + '.receipts')
                        exported = root / 'receipt.json'
                        io.export_receipt(receipt, exported)
                        repeated = io.replay(service, io.read_receipt(exported), str(project))
                        assert repeated['success'] and repeated['replay_match'], repeated
                        records['replay'] = repeated
                        (project / 'LAWS.bend').write_text('import Base\nlaw identity:\n  {0n == 1n : Nat}\n')
                        failed = json.loads(registry.dispatch('bend_verify', {'project_dir': str(project)}))
                        assert not failed['success'] and failed['verdict'] == 'fail', failed
                        records['invalid-proof'] = failed
                        shutil.copyfile(plugin / 'examples/basic/LAWS.bend', project / 'LAWS.bend')
                        helper = b'import Base\n\ndef zero() -> Nat:\n  0n\n'
                        manifest = (hashlib.sha256(helper).hexdigest() + ' Main.bend\n').encode()
                        pkg = '0x' + hashlib.sha256(manifest).hexdigest()[:32]
                        cache = root / 'hub-cache'
                        (cache / pkg).mkdir(parents=True)
                        (cache / pkg / 'Main.bend').write_bytes(helper)
                        (cache / 'names').mkdir()
                        (cache / 'names/sample@1.0.0.0').write_text(pkg + '\n')
                        (project / 'LAWS.bend').write_text('import Base\nimport sample@1.0.0.0/Main.bend as Lib\nlaw identity:\n  {Lib.zero() == 0n : Nat}\n')
                        hub = json.loads(registry.dispatch('bend_verify', {'project_dir': str(project)}))
                        assert hub['success'] and hub['dependency_packages'], hub
                        records['cached-hub'] = hub
                        records['cached-hub-replay'] = io.replay(service, hub, str(project))
                        assert records['cached-hub-replay']['success']
                        shutil.copyfile(plugin / 'examples/basic/LAWS.bend', project / 'LAWS.bend')
                        if args.aodl_project:
                            result = json.loads(registry.dispatch('bend_verify', {'project_dir': str(args.aodl_project.resolve())}))
                            assert result['success'], result
                            records['aodl'] = result
                finally:
                    reset_hermes_home_override(token)
            token = set_hermes_home_override(homes[0])
            try:
                current = registry.get_entry('bend_verify').handler.__self__
                assert current.ctx.state.get('last_receipt')['receipt_id'] != records['profile-b']['receipt_id']
                records['profile-a-return'] = json.loads(registry.dispatch('bend_verify', {'project_dir': str(project)}))
                assert records['profile-a-return']['success']
                assert records['profile-a-return']['kernel_strategy'] == 'session-pinned'
            finally:
                reset_hermes_home_override(token)
        finally:
            for manager in managers:
                manager.unload('bend')
    args.output.write_text(json.dumps(records, indent=2) + '\n')
    print(json.dumps({name: {'verdict': r['verdict'], 'duration_ms': r['duration_ms']} for name, r in records.items()}, indent=2))


if __name__ == '__main__':
    main()
