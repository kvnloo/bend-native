"""Copy local run records into the packet's raw/ tree with local paths replaced (N4), content-addressed bodies.

usage: python3 export_raw.py <runs_dir> <packet_dir> <sanitize-map.json> [run_id ...]
sanitize-map.json (local, never committed): [[local_string, placeholder], ...] applied longest-first.
Per run: raw/runs/<id>/{run.json, oracle.json, ledger.json, config.yaml, capture.jsonl, meta/*, stdout/stderr,
plugin-data/** (only the bend plugin's data), agent-log.txt (this run's lines), home-upper.list, fixture states}.
Request/response bodies go to raw/bodies/<sha256 of the sanitized bytes>.gz (deduplicated); capture.jsonl rows
reference them. All comparisons in analyze.py run on these sanitized bodies.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import sys
from pathlib import Path

META = ("argv", "config-sha256", "ended_at", "env.inside", "exit_code", "home-upper.list", "live-home-stat.after",
        "live-home-stat.before", "mask.inside", "plugin-head", "plugin-status", "started_at", "stderr", "stdout",
        "worktree-head", "worktree-status")


def main():
    runs, packet, mapping = Path(sys.argv[1]), Path(sys.argv[2]), json.loads(Path(sys.argv[3]).read_text())
    mapping = sorted(mapping, key=lambda kv: -len(kv[0]))
    only = set(sys.argv[4:])

    def clean(text: str) -> str:
        for old, new in mapping:
            text = text.replace(old, new)
        return text

    def clean_bytes(data: bytes) -> bytes:
        return clean(data.decode("utf-8", "surrogateescape")).encode("utf-8", "surrogateescape")

    bodies = packet / "raw" / "bodies"
    bodies.mkdir(parents=True, exist_ok=True)

    def store(data: bytes) -> str:
        data = clean_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        target = bodies / f"{digest}.gz"
        if not target.exists():
            target.write_bytes(gzip.compress(data, mtime=0))
        return digest

    ledger = {}
    for line in (runs / "drive-ledger.jsonl").read_text().splitlines():
        row = json.loads(line)
        ledger[row["run_id"]] = row
    for rd in sorted(p for p in runs.iterdir() if p.is_dir()):
        if only and rd.name not in only:
            continue
        if not (rd / "run.json").exists():
            continue  # in-process records are exported separately
        out = packet / "raw" / "runs" / rd.name
        if out.exists():
            shutil.rmtree(out)
        (out / "meta").mkdir(parents=True)
        for name in ("run.json", "oracle.json", "config.yaml", "session.env", "fixture-state.before.json",
                     "fixture-state.after.json"):
            if (rd / name).exists():
                (out / name).write_text(clean((rd / name).read_text(errors="replace")))
        if rd.name in ledger:
            (out / "ledger.json").write_text(clean(json.dumps(ledger[rd.name], sort_keys=True)) + "\n")
        for name in META:
            f = rd / "meta" / name
            if f.exists():
                (out / "meta" / name).write_text(clean(f.read_text(errors="replace")))
        rows = []
        cap = rd / "capture"
        if (cap / "index.jsonl").exists():
            for line in (cap / "index.jsonl").read_text().splitlines():
                row = json.loads(line)
                stem = f"{row['n']:04d}"
                row["req_body"] = store((cap / f"{stem}.req").read_bytes()) if (cap / f"{stem}.req").exists() else None
                row["resp_body"] = store((cap / f"{stem}.resp").read_bytes()) if (cap / f"{stem}.resp").exists() else None
                row["path"] = clean(row["path"])
                rows.append(row)
        (out / "capture.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
        hermes = rd / "home-upper"
        hh = next((p for p in hermes.rglob(".hermes") if p.is_dir()), None) if hermes.exists() else None
        if hh is not None:
            pdata = hh / "plugin-data"
            if pdata.exists():
                for f in sorted(pdata.rglob("*")):
                    rel = f.relative_to(pdata)
                    if f.is_file() and (rel.parts[0] == "bend" or "bend" in rel.parts[0]):
                        dest = out / "plugin-data" / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(clean_bytes(f.read_bytes()))
            log = hh / "logs" / "agent.log"
            started = (rd / "meta" / "started_at").read_text().strip() if (rd / "meta" / "started_at").exists() else ""
            if log.exists() and started:
                # the overlay copy holds template lines from setup; keep only this run's lines
                stamp = started.replace("T", " ")[:19]
                lines = [ln for ln in log.read_text(errors="replace").splitlines() if ln[:19] >= stamp]
                (out / "agent-log.txt").write_text(clean("\n".join(lines) + "\n"))
        print("exported", rd.name, len(rows))


if __name__ == "__main__":
    main()
