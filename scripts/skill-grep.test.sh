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

# Sectioned file (>=3 headings) -> the structural fallback lists its titles.
cat > "$tmp/db/knowledge/sectioned.md" <<'SEC'
# Useful Queries

### Bin Allocations for Stock Transfer
select 1;

### Invalid Email Contacts
select 2;

### Sales Order Batch Numbers
select 3;
SEC

# Regression (caught 2026-08-30): a real flat list CAN carry a few headings.
# menu-ids-reference.md has 3 of them for 923 lines; classifying it as
# "sectioned" printed 3 useless titles and left ~770 lines to search. The test is
# lines-per-section, not heading count, so this must come out FLAT.
# Shape mirrors the real file: exactly 3 headings that `^#{2,4} ` matches, ~900
# rows. If the fixture had fewer than 3 it would take the flat branch for the
# WRONG reason and the test could never fail — verified by mutation.
{
  echo "# Menu IDs"
  echo
  echo "## How to use (UI API)"
  echo "some english intro prose that must not be the sample"
  echo "## Módulos"
  for i in $(seq 1 450); do echo "- Pedido de venda $i — \`$((3000 + i))\`"; done
  echo "## Ferramentas"
  for i in $(seq 1 450); do echo "- Configurações gerais $i — \`$((8000 + i))\`"; done
} > "$tmp/di/knowledge/big-flat-with-headings.md"

# Flat file (enum dump) -> no titles, so the fallback samples rows instead.
# The prose/quote at the top must NOT be what gets sampled: these files open with
# an intro, and sampling it would hide the very rows the caller needs to see.
cat > "$tmp/di/knowledge/flat.md" <<'FLAT'
# Enums

> a quoted note that must not be sampled

an english intro paragraph that must not be the sample either

oOrders = 17
oInvoices = 13
oCreditNotes = 14
oPurchaseOrders = 22
oIncomingPayments = 24
oPriceLists = 6
oDeliveryNotes = 15
oQuotations = 23
FLAT

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
# A mem hit is a WEAK signal the caller is told to verify, so this is precisely
# where a wrong-language grep gets abandoned on a plausible-looking match. The
# retry order must survive here too, or it is not unconditional.
echo "$out" | grep -qi "OTHER language" || { echo "FAIL exit 10 (mem hit) withheld the other-language directive"; fail=1; }

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

# --- structural fallback: sectioned file lists its titles -------------------
# The point of the fallback is recall by construction: on a miss the caller must
# be shown terms that are provably in the file, in the file's own language.
out=$(SKILL_GREP_MEM=/does/not/exist "$SG" "$tmp/db/knowledge/sectioned.md" "transferência de estoque")
check $? 1 "grep miss on sectioned file -> exit 1 (unchanged)"
for want in "Bin Allocations for Stock Transfer" "Invalid Email Contacts" "Sales Order Batch Numbers"; do
  echo "$out" | grep -qF "$want" || { echo "FAIL structural fallback omitted a section: $want"; fail=1; }
done
echo "$out" | grep -qi "OTHER language" || { echo "FAIL sectioned file -> no other-language directive"; fail=1; }
echo "$out" | grep -qF "select 1;" && { echo "FAIL structural fallback dumped body lines, not just titles"; fail=1; }
# The directive must quote the term the caller actually searched for.
echo "$out" | grep -qF 'transferência de estoque' || { echo "FAIL directive did not echo the searched term"; fail=1; }

# --- a COMPLETE listing may claim completeness ------------------------------
# That claim is the whole value of the listing: absence from a complete list is
# real evidence of absence, so the label must be earned.
echo "$out" | grep -qiE "\bcomplete list" || { echo "FAIL untruncated listing did not state it is complete"; fail=1; }

# --- a TRUNCATED listing must NOT claim completeness -------------------------
# A prefix of the titles is a sample. Labelling it "the sections of this file"
# would re-run the part-implies-whole error the flat-file excerpt was cut for.
out=$(SKILL_GREP_TOC_MAX=2 SKILL_GREP_MEM=/does/not/exist "$SG" "$tmp/db/knowledge/sectioned.md" "nada")
echo "$out" | grep -qF "Bin Allocations for Stock Transfer" || { echo "FAIL truncated listing dropped the first section"; fail=1; }
echo "$out" | grep -qF "Sales Order Batch Numbers" && { echo "FAIL SKILL_GREP_TOC_MAX did not truncate"; fail=1; }
echo "$out" | grep -qE "more" || { echo "FAIL truncation was silent (no 'N more not shown')"; fail=1; }
echo "$out" | grep -qiE "\bcomplete list" && { echo "FAIL truncated listing claimed to be complete"; fail=1; }
echo "$out" | grep -qi "incomplete\|do NOT read absence" || { echo "FAIL truncated listing did not warn against reading absence into it"; fail=1; }

# --- flat file: NO excerpt, just the other-language directive ---------------
# These files are mixed-language (menu-ids-reference.md is English prose over
# pt-BR rows), so any excerpt reports the language of the excerpt, not of the
# part being searched. The retry keys off the QUERY's language instead.
out=$(SKILL_GREP_MEM=/does/not/exist "$SG" "$tmp/di/knowledge/flat.md" "pedido de venda")
echo "$out" | grep -qi "OTHER language" || { echo "FAIL flat file -> no other-language directive"; fail=1; }
echo "$out" | grep -qE "o(Invoices|CreditNotes|PurchaseOrders) = " && { echo "FAIL flat file leaked a content excerpt (language sniffing is gone)"; fail=1; }
echo "$out" | grep -qF "english intro paragraph" && { echo "FAIL flat file leaked intro prose"; fail=1; }

# --- a big flat file with a few headings is FLAT, not sectioned -------------
# Listing 3 titles for ~900 rows is not an index; the caller needs to see rows.
out=$(SKILL_GREP_MEM=/does/not/exist "$SG" "$tmp/di/knowledge/big-flat-with-headings.md" "sales order")
echo "$out" | grep -qF "## Módulos" && { echo "FAIL big flat file with 3 headings was listed as an index"; fail=1; }
echo "$out" | grep -qi "OTHER language" || { echo "FAIL big flat file -> no other-language directive"; fail=1; }
echo "$out" | grep -qE "(Pedido de venda|Configurações gerais) [0-9]+ —" && { echo "FAIL big flat file leaked a content excerpt"; fail=1; }

# --- the fallback must NOT fire when grep hit (it would be pure noise) ------
out=$(SKILL_GREP_MEM=/does/not/exist "$SG" "$tmp/db/knowledge/sectioned.md" "Invalid Email")
check $? 0 "grep hit on sectioned file -> exit 0"
echo "$out" | grep -qF "Sales Order Batch Numbers" && { echo "FAIL structure printed on a HIT (should be silent)"; fail=1; }

# --- an actually-invalid invocation must be rejected by the fake, too -------
# (guards the guard: if this ever prints "ok" instead of erroring, the fake
# stopped enforcing the real mem contract.)
if "$fake_mem" search "x" -j 2>/dev/null; then
  echo "FAIL fake-mem accepted -j (contract stopped being enforced)"; fail=1
else
  echo "ok   fake-mem still rejects an unrecognised flag like the real mem does"
fi

[ $fail -eq 0 ] && echo "ok   all skill-grep checks passed" || exit 1
