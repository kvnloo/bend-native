"""Deterministic measured plan for the contract lane (written before any measured run).

12 pair blocks; in block p every task (cua, bend, repo) runs once in each arm (off, shadow, aa) with the same
seed (5000 + p). Task order and arm order inside a block are shuffled by random.Random(20261002).
Then 6 footprint runs (plugin installed but disabled), 2 per task. usage: python3 make_plan.py > plan.json
"""
import json
import random

rng = random.Random(20261002)
runs, n = [], 0
for pair in range(1, 13):
    tasks = ["cua", "bend", "repo"]
    rng.shuffle(tasks)
    for task in tasks:
        arms = ["off", "shadow", "aa"]
        rng.shuffle(arms)
        for arm in arms:
            n += 1
            runs.append({"run_id": f"m{n:03d}-{task}-{arm}-p{pair:02d}", "task": task, "arm": arm, "pair": pair,
                         "seed": 5000 + pair, "set": "measured"})
for i, task in enumerate(["cua", "bend", "repo", "cua", "bend", "repo"], 1):
    runs.append({"run_id": f"f{i:02d}-{task}-disabled", "task": task, "arm": "disabled", "pair": 100 + i,
                 "seed": 5100 + i, "set": "footprint"})
print(json.dumps({"seed": 20261002, "runs": runs}, indent=1))
