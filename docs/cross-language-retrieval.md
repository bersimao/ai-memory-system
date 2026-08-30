# Retrieval across two languages (and a jargon)

Most teams do not work in one language. You think in one, your documentation is
written in another, and your domain has a third vocabulary of its own that is
neither. Retrieval quietly breaks on this, and the way it breaks is worse than
returning nothing: **it returns confident, plausible, wrong results.**

This is what we measured, what did not work, and what the system does about it.

Examples below come from SAP Business One, because that is the corpus this was
measured on. Nothing here is specific to it — substitute any domain whose terms
are fixed by a vendor and any pair of languages.

## Two failure modes, and they stack

### 1. Embeddings cluster by language, not only by subject

A query in Portuguese pulls Portuguese chunks *whatever they are about*.

Measured 2026-08-29 against BGE-M3, using the raw dense embedding with no BM25 or
hybrid ranking involved at all — so this is not a tuning problem in the search
layer:

| query | candidate | cosine |
|---|---|---|
| pt-BR | **wrong subject**, same language (pt-BR) | **0.71** |
| pt-BR | right subject, other language (en-US) | 0.52 |

The wrong answer in your language outranks the right answer in the other one. No
threshold fixes that ordering, because the ordering is the model's.

### 2. A word can match in the wrong sense

Measured 2026-08-30, on real memory. The pt-BR query *"quando conversamos sobre
códigos de depósito"* returned five hits, all around 0.50:

- `Posições no depósito` — a menu list
- `Deposit × IncomingPayment matching` — a **bank** deposit
- `"Não existe pagamento para esse depósito"` — a **bank** deposit again

In pt-BR, *depósito* means both **warehouse** and **bank deposit**. Every hit was
the financial sense. The warehouse memory that genuinely existed never surfaced.

Nothing in the output said any of this was weak. Five confident-looking results
read as an answer, which is exactly why silence here is more dangerous than
`(no results)`.

### And grep does not save you either

`grep` is exact-string, so it fails on the same gap from the other side. The
documented case: the query library titles its entries in English, so
`grep "transferência de estoque"` returns 0 while `grep "stock transfer"` finds
*Bin Allocations for Stock Transfer*. **A grep miss is not evidence of absence**
whenever the corpus might be in the other language.

## What we tried that did not work

### Machine translation of the query

Argos Translate (offline, no API quota) is the obvious idea: translate the query
on a miss, search again. Benchmarked 2026-08-30 on 30 real cases drawn from the
bilingual knowledge files.

**Cost turned out not to be the problem.** A default `pip install` resolves to
**5.2 GB** — torch plus the whole CUDA stack — but only because
`argostranslate/sbd.py` imports `stanza` to split sentences, which a one-line
query never needs. With `--no-deps` plus a stub for `stanza`:

| | default | slim |
|---|---|---|
| disk | 5.2 GB | **302 MB** |
| per call | 2.6 s | **0.35 s** |
| peak RSS | 1.24 GB | 219 MB |

Translations were identical, case for case.

**Accuracy was the problem — specifically on jargon:**

| strategy | hits |
|---|---|
| raw grep, no translation | 0/30 |
| translated, grep the phrase | 8/30 |
| translated, grep ALL tokens | 14/30 |
| translated, grep ANY token | 27/30, median 13 junk lines |

Three systematic error classes, all fatal for a domain corpus:

1. **Vendor terms are not dictionary terms.** `pedido de venda` → "Request for
   sale" (it is a **Sales Order**); `filiais` → "subsidiaries" (**Branches**);
   `contrato guarda-chuva` → "contract umbrella" (**Blanket Agreement**);
   `endereço de armazenagem` → "storage address" (**Bin**).
2. **Wrong regional variant.** `accounting periods` → *"períodos
   contabilísticos"* (pt-PT) where the product's own UI says *"períodos
   contábeis"* (pt-BR).
3. **Word order and synonyms** never line up with an exact title.

A general-purpose translator does not know your vendor's vocabulary, and your
vendor's vocabulary is the part that matters.

### Detecting the corpus language

Also rejected, after building it. The idea was to sample lines from the file and
show the caller what language it is in. **Knowledge files are mixed**: one file
here is English prose for 29 lines and Portuguese data rows for the next 890. Any
excerpt reports the language *of the excerpt*, not of the part being searched, so
this is right only by luck of the layout.

## What actually works

### Use the language of the query, not of the corpus

The corpus language is unknown and possibly mixed. The **query** language is
known for certain — whoever typed it knows what they typed. If the corpus is
two languages and the term missed, the other language is the one thing left to
try. No detection required.

So both retrieval entry points now say so, in code, at the moment of failure:

- **`scripts/mem`** — when the top score is `<= 0.50` (the same threshold
  `mem report` uses for "weak/no match", so the two never disagree), it prints
  the order to re-run the query in the other language. It costs nothing until it
  fires.
- **`scripts/skill-grep`** — on any grep miss, on every exit path, including the
  one where the vector tier *did* return something. That path matters most: a
  weak semantic hit is precisely what makes someone stop searching.

### Prefer a complete listing over a cleverer match

When a grep misses in a file whose entries are named sections, printing **every**
section title beats trying to guess the right synonym: ~354 tokens for a
26-entry library, about 5 ms, no dependency, and the reader picks the right one
by meaning — which humans and LLMs do far better than any string match.

The guarantee only holds while the listing is **complete and honestly scoped**:

- A **truncated** listing is a prefix sample. It is labelled as one, and says not
  to read absence into it.
- A listing is complete over **titles**, never over the file. A term can live in
  a section's body and in no title — in our corpus `WhsCode` appears on 10 lines
  and in none of the 36 titles. What proves a literal string is absent is the
  grep that already read every line; the listing is for navigation.

That distinction is not pedantry. Every bug found while building this was the
same error — **a part presented as the whole**: a few headings taken as proof of
an index, one excerpt taken as proof of a file's language, a prefix taken as
proof of absence, completeness over titles taken as completeness over content.

### Why this lives in code and not in the instructions

A line in `CLAUDE.md` costs ~30 tokens of *every* session in *every* project,
forever — and it is prose, which is the thing that gets skipped. This repo has
its own logged incident of exactly that: an agent asserted something "never
worked" while the answer sat in another project's log, with the rule to search
first sitting unread in its own instructions.

Same principle as everything else here: **if it must happen, a program has to
make it happen.** Prose in a prompt is not a guarantee.

## Adapting it

- **Different language pair** — edit the one sentence in `scripts/mem` and
  `scripts/skill-grep`. Nothing else knows about pt-BR or en-US.
- **Different threshold** — `0.50` is calibrated for the current embedding model.
  Change it in `scripts/mem` *and* in `mem report` together; a test pins the
  boundary so they cannot drift apart silently.
- **More than two languages** — the "one thing left to try" argument stops
  holding, and the retry order should name the candidates explicitly.
