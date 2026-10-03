#!/usr/bin/env bash
# stage.sh <bend checkout at 17db447a> <packet dir> <stage dir> <bend-native clone>
# The bend checkout must also hold refs/b389/upstream-e1ed2435 (upstream fix commit for issue 1212, fetched read-only).
# Builds the case lists and one private corpus copy per (arm, mode). Plain file copies only.
set -euo pipefail
SRC="$(cd "$1" && pwd)"; PKT="$(cd "$2" && pwd)"; ST="$3"; BN="$4"
UP=e1ed243502764d013471a7338bc7ab2c8e62ff98; PLUGIN_SHA=e85e65e5d2e11caba8412d6dbd19aad03fd785ad
[ "$(git -C "$SRC" rev-parse HEAD)" = 17db447a8b8c17b51517de42b35d3216e563f40f ] || { echo "corpus must be 17db447a" >&2; exit 2; }
[ -z "$(git -C "$SRC" status --porcelain)" ] || { echo "corpus checkout not clean" >&2; exit 2; }
mkdir -p "$ST/lists"
# cases derived from the corpus checkout (not committed in the packet)
CS="$ST/cases"; rm -rf "$CS"; cp -a "$PKT/cases" "$CS"
mkdir -p "$CS/s1212_prlaw" "$CS/s1212_prlaw_nolaw" "$CS/f1193"
cp "$SRC"/tests/proof/group_name_collision_semantics.bend "$SRC"/tests/proof/group_name_collision_semantics_lib.bend "$CS/s1212_prlaw/"
cp "$SRC"/tests/proof/group_name_collision_semantics_lib.bend "$CS/s1212_prlaw_nolaw/"
# the same program without its law and proof: main's certified value in the PR's own layout
sed '/^law main_is_false:/,$d' "$SRC"/tests/proof/group_name_collision_semantics.bend > "$CS/s1212_prlaw_nolaw/group_name_collision_semantics.bend"
cp "$SRC"/tests/check/conversion_shared_cells_once.bend "$CS/f1193/"
# the upstream fix's own regression test (bendlang/bend PR 1215, unreleased), with and without its laws
mkdir -p "$CS/s1212_up_dead_forward" "$CS/s1212_up_dead_forward_nolaw"
git -C "$SRC" show "$UP:tests/proof/group_dead_forward.bend" > "$CS/s1212_up_dead_forward/group_dead_forward.bend"
python3 - "$CS/s1212_up_dead_forward/group_dead_forward.bend" "$CS/s1212_up_dead_forward_nolaw/group_dead_forward.bend" <<'PY'
import re, sys
src = open(sys.argv[1]).read()
# drop each `law X:` block and its `def X():\n  {==}` proof; keep everything else
src = re.sub(r"\nlaw (\w+):\n(?:  .*\n)+\ndef \1\(\):\n  \{==\}\n", "\n", src)
open(sys.argv[2], "w").write(src)
PY
# the pinned plugin, exported from the bend-native clone (never the lane worktree)
rm -rf "$ST/plugin-$PLUGIN_SHA"; mkdir -p "$ST/plugin-$PLUGIN_SHA"
git -C "$BN" archive "$PLUGIN_SHA" | tar -x -C "$ST/plugin-$PLUGIN_SHA"
(cd "$SRC" && find tests -name '*.bend' -type f | LC_ALL=C sort) > "$ST/lists/corpus-tests.txt"
(cd "$SRC" && find demos evals -name '*.bend' -type f | LC_ALL=C sort) > "$ST/lists/corpus-examples.txt"
(cd "$CS" && find . -name '*.bend' -type f | sed 's#^\./#b389cases/#' | LC_ALL=C sort \
   | grep -vE '/(a-b|group_name_collision_semantics_lib)\.bend$|^b389cases/bootstrap/') > "$ST/lists/special.txt"
for arm in A B C1 C2; do
  for mode in direct plugin; do
    R="$ST/root-$arm-$mode"
    [ -e "$R" ] && continue
    mkdir -p "$R"
    cp -a "$SRC/tests" "$SRC/demos" "$SRC/evals" "$R/"
    cp -a "$CS" "$R/b389cases"
  done
done
# hermes bend verify fixtures: each special case's directory with its main file as PROOF.bend
rm -rf "$ST/cli-fixtures"
while read -r c; do
  id=$(basename "$(dirname "$c")"); mkdir -p "$ST/cli-fixtures/$id"
  cp -a "$CS/$id/." "$ST/cli-fixtures/$id/"
  mv "$ST/cli-fixtures/$id/$(basename "$c")" "$ST/cli-fixtures/$id/PROOF.bend"
done < "$ST/lists/special.txt"
wc -l "$ST"/lists/*.txt
