"""Use the existing DecisionBackend scorer in an explicitly selected z0 environment."""
import importlib.util
import json
import sys
from pathlib import Path

request = json.loads(Path(sys.argv[1]).read_text())
spec = importlib.util.spec_from_file_location('bend_shadow_scorer', Path(__file__).parent / 'observer/shadow_api_failure.py')
scorer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scorer)
examples = scorer.read_jsonl(Path(request['examples']))
print(json.dumps(scorer.score_examples(examples, request['backend'], Path(request['root']))))
