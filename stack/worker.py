"""Isolated entry point for the bundled, unmodified z0 reducers."""
import json
import sys
from pathlib import Path

# Only this child receives the bundled z0 package on its import path.
sys.path.insert(0, str(Path(__file__).parent / 'vendor'))
from z0int.state_packet import build_state_packet
from z0int.decision_opportunity import build_decision_opportunity, deterministic_gate

request = json.loads(Path(sys.argv[1]).read_text())
packet = build_state_packet(request['repo'], intent='status', adapters=('git', 'docs'),
                            projects_root=request['projects_root'], github=False)
result = {'packet': packet}
if request.get('request'):
    opportunity = build_decision_opportunity(request['repo'], request['request'], packet=packet,
                                             harness='hermes', trace_id=request.get('trace_id'))
    result.update(opportunity=opportunity, gate=deterministic_gate(opportunity))
print(json.dumps(result))
