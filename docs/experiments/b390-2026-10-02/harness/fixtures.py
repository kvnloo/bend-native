"""B390 fixtures: BendHub packages, a static private hub tree, and proof projects.

Package identity follows Bend's own publish rule (bend2/main.ts cli_publish):
  manifest = ''.join(sha256(file) + ' ' + path + '\n' for path in sorted(files))
  package  = '0x' + sha256(manifest)[:32]
The hub serves <pkg>/manifest, <pkg>/<path> and name/<name>@<version>, which is what
Bend's hub_get/name_hash read. Bend checks every fetched byte against these hashes,
so a successful Bend fetch from this tree independently confirms the identities.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

LICENSE = b"MIT No Attribution\n\nFixture package for the B390 dependency-provenance lane.\n"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest(files: dict[str, bytes]) -> bytes:
    return "".join(sha(files[p]) + " " + p + "\n" for p in sorted(files)).encode()


def package_id(files: dict[str, bytes]) -> str:
    return "0x" + sha(manifest(files))[:32]


def input_manifest(files: dict[str, bytes]) -> str:
    """Independent re-implementation of the receipt's documented input-manifest digest:
    sha256 over sorted (relative path, NUL, sha256(bytes) raw digest, LF) records."""
    digest = hashlib.sha256()
    for rel, data in sorted(files.items()):
        digest.update(rel.encode("utf-8") + b"\0" + hashlib.sha256(data).digest() + b"\n")
    return digest.hexdigest()


def pkg_util(value: str) -> dict[str, bytes]:
    """Two-file package: Main.bend imports ./Util.bend inside the package."""
    return {
        "LICENSE": LICENSE,
        "Main.bend": b"import Base\nimport ./Util.bend as U\n\ndef zero() -> Nat:\n  U.base()\n",
        "Util.bend": ("import Base\n\ndef base() -> Nat:\n  " + value + "\n").encode(),
    }


def pkg_direct(value: str, tag: str) -> dict[str, bytes]:
    return {
        "LICENSE": LICENSE,
        "Main.bend": ("import Base\n# " + tag + "\n\ndef zero() -> Nat:\n  " + value + "\n").encode(),
    }


def pkg_transitive(inner: str) -> dict[str, bytes]:
    """Named-package payload that imports a content-hash package (transitive Hub closure)."""
    return {
        "LICENSE": LICENSE,
        "Main.bend": ("import Base\nimport " + inner + "/Main.bend as P\n\ndef zero() -> Nat:\n  P.zero()\n").encode(),
    }


def build():
    P_ok = pkg_util("0n")             # hash kind, law holds
    P_bad = pkg_util("1n")            # hash kind, law fails (forging discriminator)
    p_ok, p_bad = package_id(P_ok), package_id(P_bad)
    Q_ok = pkg_transitive(p_ok)       # named kind, transitive -> P_ok, law holds
    Q_alt = pkg_direct("0n", "alternate package for the same name@version")  # law holds, different bytes
    Q_bad = pkg_direct("1n", "failing package")                              # law fails
    packages = {"P_ok": P_ok, "P_bad": P_bad, "Q_ok": Q_ok, "Q_alt": Q_alt, "Q_bad": Q_bad}
    ids = {k: package_id(v) for k, v in packages.items()}
    names = {"sample@1.0.0.0": ids["Q_ok"], "bad@1.0.0.0": ids["Q_bad"]}
    proof = b"import Base\nimport ./LAWS.bend as Laws\n\ndef Laws.identity():\n  {==}\n"

    def laws(target: str) -> bytes:
        return ("import Base\nimport " + target + "/Main.bend as Lib\n\nlaw identity:\n  {Lib.zero() == 0n : Nat}\n").encode()

    projects = {
        "H": {"LAWS.bend": laws(ids["P_ok"]), "PROOF.bend": proof},       # content-hash import, pass
        "HF": {"LAWS.bend": laws(ids["P_bad"]), "PROOF.bend": proof},     # content-hash import, fail
        "N": {"LAWS.bend": laws("sample@1.0.0.0"), "PROOF.bend": proof},  # named import, pass
        "NF": {"LAWS.bend": laws("bad@1.0.0.0"), "PROOF.bend": proof},    # named import, fail
    }
    # The closure each project needs in a dependency cache.
    closure = {
        "H": {"packages": ["P_ok"], "names": {}},
        "HF": {"packages": ["P_bad"], "names": {}},
        "N": {"packages": ["Q_ok", "P_ok"], "names": {"sample@1.0.0.0": "Q_ok"}},
        "NF": {"packages": ["Q_bad"], "names": {"bad@1.0.0.0": "Q_bad"}},
    }
    return packages, ids, names, projects, closure


def write_tree(root: Path, files: dict[str, bytes]) -> None:
    for rel, data in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def write_hub(root: Path, packages, ids, names) -> None:
    """Static hub tree as served by Bend's hub (GET <pkg>/manifest, <pkg>/<path>, name/<nv>)."""
    for key, files in packages.items():
        pkg = ids[key]
        write_tree(root / pkg, files)
        (root / pkg / "manifest").write_bytes(manifest(files))
    for nv, pkg in names.items():
        (root / "name").mkdir(parents=True, exist_ok=True)
        (root / "name" / nv).write_text(pkg + "\n")


def write_cache(root: Path, packages, ids, keys, name_map) -> None:
    """A dependency cache in Bend's BEND_LIB layout (what a Bend fetch writes)."""
    root.mkdir(parents=True, exist_ok=True)
    for key in keys:
        write_tree(root / ids[key], packages[key])
    for nv, key in name_map.items():
        (root / "names").mkdir(parents=True, exist_ok=True)
        (root / "names" / nv).write_text(ids[key] + "\n")


def describe() -> dict:
    packages, ids, names, projects, closure = build()
    return {
        "packages": {k: {"id": ids[k], "manifest_sha256": sha(manifest(v)),
                         "files": {p: sha(b) for p, b in sorted(v.items())}} for k, v in packages.items()},
        "hub_names": names,
        "projects": {k: {p: sha(b) for p, b in sorted(v.items())} for k, v in projects.items()},
        "closure": closure,
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 4 and sys.argv[1] == "project":
        write_tree(Path(sys.argv[3]), build()[3][sys.argv[2]])
    else:
        print(json.dumps(describe(), indent=2))
