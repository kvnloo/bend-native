"""Capture existing Bend Hub packages without resolving or downloading new code."""
from __future__ import annotations

from pathlib import Path
import re

from .verify_core import (
    BendVerifyError, _MAX_INPUT_BYTES, _MAX_INPUT_FILES, _local_candidate,
    _local_imports, _manifest, _read_stable, _reject_symlink_components, sha256_bytes,
)

_PACKAGE = re.compile(r'0x[0-9a-f]{32}')
_NAME = re.compile(r'[a-z][a-z0-9-]{0,63}@(0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){3}')


class Dependencies:
    def __init__(self, cache):
        self.cache = Path(cache).expanduser().resolve() if cache else None
        self.files = {}
        self.packages = {}
        self.names = {}
        self._seen = set()
        self._bytes = 0

    def _put(self, name, data):
        if name not in self.files:
            self._bytes += len(data)
            if len(self.files) >= _MAX_INPUT_FILES or self._bytes > _MAX_INPUT_BYTES:
                raise BendVerifyError('input_too_large', 'Hub dependencies exceed the capture budget')
            self.files[name] = data

    def _package(self, package):
        if package in self.packages:
            return
        root = self.cache / package
        _reject_symlink_components(self.cache, root, what='Hub package')
        if not root.is_dir():
            raise BendVerifyError('dependency_missing', f'Hub package {package} is not cached; fetch it with Bend before verification')
        contents = {}
        for entry_count, path in enumerate(root.rglob('*')):
            if entry_count >= _MAX_INPUT_FILES * 4:
                raise BendVerifyError('input_too_large', 'Hub package has too many directory entries')
            _reject_symlink_components(root, path, what='Hub package file')
            if path.is_file():
                name = path.relative_to(root).as_posix()
                data = _read_stable(path)
                self._put(f'{package}/{name}', data)
                contents[name] = data
        # Same sorted manifest used by Bend's cli_publish; includes LICENSE files.
        manifest = ''.join(sha256_bytes(data) + ' ' + name + '\n'
                           for name, data in sorted(contents.items())).encode()
        digest = sha256_bytes(manifest)
        if not contents or '0x' + digest[:32] != package:
            raise BendVerifyError('dependency_integrity', f'Cached bytes do not match Hub package {package}')
        self.packages[package] = digest

    def include(self, target):
        if self.cache is None:
            raise BendVerifyError('unsupported_import', 'Bend Hub imports require a configured dependency cache')
        package, _, relative = target.partition('/')
        if '@' in package:
            if not _NAME.fullmatch(package):
                raise BendVerifyError('unsupported_import', 'Invalid named Hub import')
            name = package
            mapping = self.cache / 'names' / name
            _reject_symlink_components(self.cache, mapping, what='Hub name binding')
            try:
                package = _read_stable(mapping).decode('utf-8').strip()
            except (OSError, UnicodeError) as exc:
                raise BendVerifyError('dependency_missing', f'No usable cached binding for {name}') from exc
            if not _PACKAGE.fullmatch(package):
                raise BendVerifyError('dependency_integrity', f'Invalid cached package identity for {name}')
            previous = self.names.setdefault(name, package)
            if previous != package:
                raise BendVerifyError('input_unstable', f'Hub binding changed during capture: {name}')
            self._put('names/' + name, (package + '\n').encode())
        if not _PACKAGE.fullmatch(package):
            raise BendVerifyError('unsupported_import', 'Hub imports require a full 32-hex package identity')
        self._package(package)
        candidate = _local_candidate(self.cache, self.cache / package, relative)
        key = candidate.relative_to(self.cache).as_posix()
        if key not in self.files or not key.startswith(package + '/'):
            raise BendVerifyError('unsupported_import', 'Hub import is outside its captured package')
        if key in self._seen:
            return
        self._seen.add(key)
        try:
            text = self.files[key].decode('utf-8')
        except UnicodeError as exc:
            raise BendVerifyError('invalid_input', f'Hub input is not UTF-8: {key}') from exc
        for target in _local_imports(text, allow_hub=True):
            first = target.split('/')[0]
            if first.startswith('0x') or '@' in first:
                self.include(target)
            else:
                child = _local_candidate(self.cache / package, candidate.parent, target)
                self.include(package + '/' + child.relative_to(self.cache / package).as_posix())

    def identity(self):
        return {'files': self.files, 'manifest_sha256': _manifest(self.files),
                'packages': self.packages, 'names': self.names}
