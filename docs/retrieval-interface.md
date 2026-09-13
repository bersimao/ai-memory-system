# Retrieval interface

`mem search QUERY [-k N] [-c curated|transcripts] [--scope all|project]
[--project PATH] [--domain SKILL] [--semantic] [--json] [--session ID]`

The default scope is private `automatic_recall_scope` configuration. `all` means all
private project contexts plus shared global and skill knowledge/references. `project`
uses the pinned/git-root anchor plus shared sources. Domain narrows skill knowledge;
it does not classify or restrict private project documents. Scope is a selection rule
for a trusted local user, not multi-user access control.

Curated searches exclude raw transcripts. Checkpoints and distilled candidates remain
unverified and carry provenance. Project labels use stable store identifiers; legacy
Codex imports retain explicit original project names.

Human output retains `N. [score] REQUEST_ID:RESULT_ID  SOURCE` plus a labeled preview.
JSON includes source paths, revision hashes, source lines, project, engine, request ID,
and result ID. `mem expand REQUEST_ID:RESULT_ID` checks membership and file revision
before returning content. Old bare chunk hashes must be replaced by a new search.

Automatic prompt recall uses lexical search and includes expansion references. Try
identifiers, synonyms, English/Portuguese alternatives, then `--semantic` on misses.
The semantic adapter filters before retrieval and reranking and rejects stale text.

`mem feedback REQUEST_ID:RESULT_ID relevant|irrelevant|used|code_passed|code_failed`
records explicit observations; `mem report` keeps them separate from result opens.
Query text is hashed in telemetry; result source metadata remains private.
