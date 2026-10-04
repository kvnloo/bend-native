"""Native composition; downstream reducers and canonical z0 service own semantics."""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

from hermes_constants import get_hermes_home
from ..verify_core import run_process, clean_env
from . import observer
from .observer.shadow_api_failure import joined_examples
from .observer.evaluate_shadow import evaluate
from .observer.decision_hooks import classify, turn_behaviour


_PATH_RE = re.compile(r"(?<![\w.~/-])((?:~|/)[^\s'\"`<>|;,)\]}]+)")
_BROAD = {'/', '/tmp', '/mnt', '/workspace', '/home', '/usr', '/opt', '/var', '/etc'}


def _repo_root(path):
    """Git toplevel for a directory, else the directory itself; None if unusable."""
    try:
        path = Path(os.path.expanduser(str(path))).resolve()
    except (OSError, RuntimeError):
        return None
    if path.is_file():
        path = path.parent
    if not path.is_dir() or str(path) in _BROAD or path == Path.home().resolve():
        return None
    try:
        top = subprocess.run(['git', '-C', str(path), 'rev-parse', '--show-toplevel'], capture_output=True,
                             text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        top = ''
    return Path(top).resolve() if top else path


def resolve_task_repo(text, payload):
    """Pick the repository the task is about, with the evidence for the choice.

    Order: an explicit hook value, then an existing directory the user's request names, then the
    process working directory. The process cwd is only a fallback because one Hermes process
    serves many tasks. Returns (path, source)."""
    for key in ('cwd', 'workdir', 'repo'):
        found = _repo_root(payload.get(key)) if payload.get(key) else None
        if found:
            return str(found), 'hook:' + key
    for match in _PATH_RE.findall(text or ''):
        found = _repo_root(match.rstrip('.:?!'))
        if found:
            return str(found), 'request_path'
    for source, value in (('TERMINAL_CWD', os.environ.get('TERMINAL_CWD')), ('process_cwd', os.getcwd())):
        found = _repo_root(value) if value else None
        if found:
            return str(found), source
    return os.getcwd(), 'process_cwd_unvalidated'


class Stack:
    def __init__(self, ctx):
        self.ctx = ctx
        self.pending = queue.Queue(maxsize=256)
        self.worker = None
        self.lock = threading.Lock()
        self.closed = False
        self.dropped = 0
        self.error = None

    def home(self):
        return get_hermes_home().resolve() / 'plugin-data' / 'bend' / 'z0'

    def enabled(self):
        return self.ctx.get_config('stack_mode', 'off') == 'shadow'

    def callback(self, event):
        def record(**payload):
            if not self.enabled() or self.closed:
                return None
            try:
                row = observer._row(event, payload)
                # Ambient lab environment flags cannot opt this native plugin into raw capture.
                row.pop('request', None)
                row.pop('response', None)
                if event == 'post_tool_call' and payload.get('tool_name') == 'bend_verify':
                    raw = payload.get('result')
                    receipt = json.loads(raw) if isinstance(raw, str) and len(raw) <= 131072 else raw
                    if isinstance(receipt, dict) and receipt.get('verification_scope') == 'bend-emitted-book':
                        row['bend_evidence'] = {key: receipt[key] for key in (
                            'receipt_id', 'success', 'verdict', 'verification_scope',
                            'source_semantics_attested', 'input_manifest_sha256', 'kernel_sha256_after')
                            if key in receipt}
                        row['label_kind'] = 'scoped_proof_evidence_not_task_success'
                if len(json.dumps(row)) > 65536:
                    raise ValueError('Observer row exceeds 64 KiB')
                home = self.home()
                with self.lock:
                    if self.closed:
                        return None
                    if self.worker is None:
                        self.worker = threading.Thread(target=self._drain, daemon=True, name='bend-z0-stack')
                        self.worker.start()
                    projection = None
                    if event == 'pre_llm_call' and self.ctx.get_config('stack_opportunities', False):
                        excluded, text = classify(payload.get('user_message'), payload.get('platform', ''))
                        if not excluded and text and len(text) <= 8192 and payload.get('turn_id'):
                            repo, repo_source = resolve_task_repo(text, payload)
                            projection = {'repo': repo, 'repo_source': repo_source,
                                          'request': text, 'trace_id': row['identity']['trace_id']}
                    if event == 'post_llm_call':
                        row['behaviour'] = turn_behaviour(payload.get('conversation_history'))
                        row['label_kind'] = 'observed_behaviour_not_optimal'
                    self.pending.put_nowait((home, row, projection))
            except queue.Full:
                self.dropped += 1
            except Exception as exc:
                self.error = type(exc).__name__
            return None
        return record

    def _drain(self):
        while True:
            try:
                job = self.pending.get(timeout=0.5)
            except queue.Empty:
                if self.closed:
                    return
                continue
            try:
                if job is None:
                    return
                home, row, projection = job
                home.mkdir(parents=True, exist_ok=True)
                with (home / 'events.jsonl').open('a') as stream:
                    stream.write(json.dumps(row) + '\n')
                if projection:
                    repo_source = projection.pop('repo_source', None)
                    result = self.project(**projection, home=home)
                    record = {'schema': 'z0int.hermes.opportunity_record.v0',
                              'session_id': row['identity']['session_id'],
                              'repo': str(Path(projection['repo']).resolve()), 'repo_source': repo_source,
                              'opportunity': result['opportunity'], 'gate': result['gate']}
                    with (home / 'opportunities.jsonl').open('a') as stream:
                        stream.write(json.dumps(record) + '\n')
            except Exception as exc:
                self.error = type(exc).__name__
                self.dropped += 1
            finally:
                self.pending.task_done()

    def close(self):
        with self.lock:
            self.closed = True
            if self.worker is not None:
                try:
                    self.pending.put_nowait(None)
                except queue.Full:
                    # The worker exits when the accepted queue drains.
                    pass
        if self.worker is not None:
            self.worker.join(timeout=2)

    def project(self, repo, request=None, trace_id=None, home=None):
        home = home or self.home()
        home.mkdir(parents=True, exist_ok=True)
        # Explicitly exclude ambient personal transcript stores in this native slice.
        projects = home / 'empty-transcripts'
        projects.mkdir(exist_ok=True)
        env = clean_env()
        env['Z0INT_HOME'] = str(home)
        with tempfile.TemporaryDirectory(prefix='bend-z0-') as scratch:
            path = Path(scratch) / 'request.json'
            path.write_text(json.dumps({'repo': str(Path(repo).resolve()), 'request': request,
                                        'trace_id': trace_id, 'projects_root': str(projects)}))
            result = run_process([sys.executable, str(Path(__file__).with_name('worker.py')), str(path)],
                                 env=env, timeout=30, check=True)
        return json.loads(result.stdout)

    def report(self):
        path = self.home() / 'events.jsonl'
        # Report is a bounded snapshot; inference is an explicit offline operation.
        if path.exists() and path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('Event log exceeds 16 MiB; archive it before reporting')
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        examples = list(joined_examples(rows))
        return {'schema': 'bend.z0_stack_report.v1', 'events': len(rows), 'examples': examples,
                'queue_pending': self.pending.qsize(), 'rows_dropped': self.dropped,
                'last_error': self.error, 'verified_task_success': None,
                'opportunities_path': str(self.home() / 'opportunities.jsonl')}

    def runtime(self, operation, payload):
        # z0 owns dispatch, execution, AODL authority and canonical receipts.
        # No implicit provider selection, paid fallback, or automatic retries here.
        routes = {'providers': '/v1/providers', 'route': '/v1/intelligence',
                  'worker': '/v1/worker', 'automatic': '/v1/automatic',
                  'consumed': '/v1/automatic/consumed'}
        port = self.ctx.get_config('stack_service_port', 11501)
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError('stack_service_port must be an integer TCP port')
        body = None if operation == 'providers' else json.dumps(payload).encode()
        if body is not None and len(body) > 40000:
            raise ValueError('z0 request exceeds service limit')
        request = urllib.request.Request(f'http://127.0.0.1:{port}{routes[operation]}', data=body,
                                         headers={'Content-Type': 'application/json'})
        # Disable ambient HTTP proxy routing of private local requests.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=25) as response:
            data = response.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ValueError('z0 response exceeds 1 MiB')
        return json.loads(data)


def setup_parser(parser):
    sub = parser.add_subparsers(dest='stack_command', required=True)
    sub.add_parser('status')
    child = sub.add_parser('report')
    child.add_argument('--examples-output', help='Create a JSONL example export; refuses overwrite')
    for command in ('state', 'opportunity'):
        child = sub.add_parser(command)
        child.add_argument('repo')
        if command == 'opportunity':
            child.add_argument('request')
            child.add_argument('--trace-id')
    child = sub.add_parser('score')
    child.add_argument('examples', help='JSONL examples from hermes z0 report')
    child.add_argument('--backend', required=True)
    child.add_argument('--z0-root', required=True)
    child.add_argument('--z0-home', required=True, help='Explicit canonical z0 state and model configuration directory')
    child.add_argument('--python', required=True, dest='runtime_python')
    child = sub.add_parser('evaluate')
    child.add_argument('scored_examples', help='JSONL scored by an existing z0 DecisionBackend')
    child = sub.add_parser('runtime')
    child.add_argument('operation', choices=('providers', 'route', 'worker', 'automatic', 'consumed'))
    child.add_argument('--request-file', help='JSON object using the canonical z0 service request schema')


def run(stack, args):
    try:
        command = args.stack_command
        if command == 'status':
            sources = json.loads(Path(__file__).with_name('sources.json').read_text())
            result = {'mode': stack.ctx.get_config('stack_mode', 'off'), 'state_home': str(stack.home()),
                      'sources': sources, 'model_runtime_loaded': False}
        elif command == 'report':
            result = stack.report()
            if args.examples_output:
                with Path(args.examples_output).open('x') as stream:
                    for row in result['examples']:
                        stream.write(json.dumps(row) + '\n')
        elif command == 'score':
            examples = Path(args.examples).resolve()
            if examples.stat().st_size > 1024 * 1024:
                raise ValueError('Score input exceeds 1 MiB; split it into smaller batches')
            root = Path(args.z0_root).resolve()
            if not (root / 'src/z0int/backends/registry.py').is_file():
                raise ValueError('z0-root must contain the existing z0int backend runtime')
            env = clean_env()
            env['Z0INT_HOME'] = str(Path(args.z0_home).expanduser().resolve())
            with tempfile.TemporaryDirectory(prefix='bend-z0-score-') as scratch:
                path = Path(scratch) / 'request.json'
                path.write_text(json.dumps({'examples': str(examples), 'backend': args.backend, 'root': str(root)}))
                result = run_process([args.runtime_python, str(Path(__file__).with_name('score_worker.py')), str(path)],
                                     env=env, timeout=120, check=True)
            result = json.loads(result.stdout)
        elif command == 'evaluate':
            path = Path(args.scored_examples)
            if path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError('Scored examples exceed 16 MiB')
            result = evaluate(json.loads(line) for line in path.read_text().splitlines())
        elif command == 'runtime':
            payload = json.loads(Path(args.request_file).read_text()) if args.request_file else {}
            if not isinstance(payload, dict):
                raise ValueError('Runtime request must be a JSON object')
            if args.operation != 'providers' and not args.request_file:
                raise ValueError('Runtime mutations require --request-file')
            result = stack.runtime(args.operation, payload)
        else:
            result = stack.project(args.repo, getattr(args, 'request', None), getattr(args, 'trace_id', None))
        code = 0
    except Exception as exc:
        result = {'success': False, 'error': str(exc), 'code': 'stack_error'}
        code = 2
    if args.stack_command == 'score' and code == 0:
        for row in result:
            print(json.dumps(row))
    else:
        print(json.dumps(result, indent=2))
    raise SystemExit(code)
