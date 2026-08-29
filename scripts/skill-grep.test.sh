#!/usr/bin/env bash
# Self-check for skill-grep: bash skill-grep.test.sh
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SG="$HERE/skill-grep"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fail=0
check() { [ "$1" -eq "$2" ] && echo "ok   $3" || { echo "FAIL $3 (expected exit $2, got $1)"; fail=1; }; }

# --- fixtures --------------------------------------------------------------
mkdir -p "$tmp/db/knowledge" "$tmp/di/knowledge"
echo "### OCRD — Business Partners" > "$tmp/db/knowledge/tables-reference.md"
echo "no relevant heading here" > "$tmp/db/knowledge/other.md"

# Fake `mem` that validates its args the same way the real one does (search,
# -k N, -c curated|transcripts, nothing else) — a regression that sends the
# real mem an unrecognised flag (it happened once: -j, which mem rejects with
# exit 2) must fail THIS test, not just get caught by hand later.
make_fake_mem() {  # make_fake_mem <path> <plain-text-body>
  cat > "$1" <<FAKE
#!/usr/bin/env bash
[ "\$1" = search ] || { echo "fake-mem: expected 'search'" >&2; exit 2; }
shift; shift  # cmd, query
while [ \$# -gt 0 ]; do
  case "\$1" in
    -k) shift 2 ;;
    -c) case "\$2" in curated|transcripts) shift 2 ;; *) echo "fake-mem: bad -c '\$2'" >&2; exit 2 ;; esac ;;
    *) echo "fake-mem: unexpected arg '\$1' — skill-grep must not forward raw flags" >&2; exit 2 ;;
  esac
done
cat <<'BODY'
$2
BODY
FAKE
  chmod +x "$1"
}

# --- usage -------------------------------------------------------------
"$SG" 2>/dev/null; check $? 64 "no args -> usage"
"$SG" "$tmp/db/knowledge/tables-reference.md" 2>/dev/null; check $? 64 "missing term -> usage"
"$SG" "$tmp/nope.md" "x" >/dev/null 2>&1; check $? 64 "missing file -> usage"

# --- grep hit: zero-cost path, never touches mem ----------------------------
out=$(SKILL_GREP_MEM=/does/not/exist "$SG" "$tmp/db/knowledge/tables-reference.md" "OCRD")
st=$?
check $st 0 "grep hit -> exit 0"
echo "$out" | grep -q "OCRD" || { echo "FAIL grep hit -> prints the match"; fail=1; }

# --- grep miss, mem not installed -------------------------------------------
out=$(SKILL_GREP_MEM=/does/not/exist "$SG" "$tmp/db/knowledge/other.md" "stock transfer")
st=$?
check $st 1 "grep miss + no mem -> exit 1"
echo "$out" | grep -qi "not installed" || { echo "FAIL grep miss + no mem -> says so"; fail=1; }

# --- grep miss, mem present, one in-skill + one other-skill result ----------
# Real `mem search` text contract: "N. [score] hash  source" then one
# indented preview line, per docs/retrieval-interface.md + scripts/mem.
fake_mem="$tmp/fake-mem-hit"
make_fake_mem "$fake_mem" "1. [0.9100] abc123  $tmp/db/knowledge/tables-reference.md
   OWTQ header fields
2. [0.9500] def456  $tmp/di/knowledge/objtype-enums.md
   unrelated skill hit"
out=$(SKILL_GREP_MEM="$fake_mem" "$SG" "$tmp/db/knowledge/other.md" "stock transfer")
st=$?
check $st 10 "grep miss + mem hit in-skill -> exit 10"
echo "$out" | grep -q "tables-reference.md" || { echo "FAIL in-skill hit is printed"; fail=1; }
echo "$out" | grep -q "objtype-enums.md" && { echo "FAIL other-skill hit leaked through the filter"; fail=1; }

# --- grep miss, mem present, only other-skill results -> true miss ---------
fake_mem_nohit="$tmp/fake-mem-nohit"
make_fake_mem "$fake_mem_nohit" "1. [0.9500] def456  $tmp/di/knowledge/objtype-enums.md
   unrelated skill hit"
out=$(SKILL_GREP_MEM="$fake_mem_nohit" "$SG" "$tmp/db/knowledge/other.md" "stock transfer")
check $? 2 "grep miss + mem hit outside skill -> exit 2 (filtered to nothing)"

# --- grep miss, mem present, no results -------------------------------------
fake_mem_none="$tmp/fake-mem-none"
make_fake_mem "$fake_mem_none" "(no results)"
out=$(SKILL_GREP_MEM="$fake_mem_none" "$SG" "$tmp/db/knowledge/other.md" "stock transfer")
check $? 2 "grep miss + mem '(no results)' -> exit 2"

# --- grep miss, mem present, empty output -----------------------------------
fake_mem_empty="$tmp/fake-mem-empty"
printf '#!/usr/bin/env bash\n' > "$fake_mem_empty"
chmod +x "$fake_mem_empty"
out=$(SKILL_GREP_MEM="$fake_mem_empty" "$SG" "$tmp/db/knowledge/other.md" "stock transfer")
check $? 2 "grep miss + mem returns nothing -> exit 2"

# --- an actually-invalid invocation must be rejected by the fake, too -------
# (guards the guard: if this ever prints "ok" instead of erroring, the fake
# stopped enforcing the real mem contract.)
if "$fake_mem" search "x" -j 2>/dev/null; then
  echo "FAIL fake-mem accepted -j (contract stopped being enforced)"; fail=1
else
  echo "ok   fake-mem still rejects an unrecognised flag like the real mem does"
fi

[ $fail -eq 0 ] && echo "ok   all skill-grep checks passed" || exit 1
