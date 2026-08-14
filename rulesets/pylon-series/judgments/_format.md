# Judgment records

One file per reviewed batch: `YYYY-MM-DD-batch-NN.md`. The author's verdicts are the
only thing that revises the rules, so they are part of the record — the loop is
rules → pieces → judgment → next revision of the rules, and this directory is the
third arrow.

**A verdict is about the rules, never about the piece's fate.** Every spec the
validator accepts is part of the series and goes out in its turn; judgment cannot
remove it. So the question a verdict answers is not "is this one good enough to
show" but "did the rules that made it need changing". A piece that a later revision
would no longer produce stays exactly where it is — that is the record, and it is
what makes the revisions legible afterwards.

Format, one table per batch:

| piece | verdict | note |
|---|---|---|
| 012 | holds | — |
| 013 | open | what it exposed, in the author's words where possible |
| 014 | revise | what was changed in response, and where |

Verdicts: **holds** (the rules held — the piece asks nothing of them), **revise**
(the rules, the renderer, or a threshold changed in response; say what and where),
**open** (something worth changing, seen but not yet acted on — it carries to a later
revision). Below the table, note any rules revision the batch triggered, with the
resulting `rules_sha`.

**An `open` verdict has one address: `notes.md`, under "Not yet variables".** To see
what is outstanding, `rg -n '\| open \|' rulesets/pylon-series/judgments/2026-*.md`
(dated files only — this one is a template and its rows are examples). An entry there
with no counterpart in the carrier is the failure this rule prevents. A batch
file is read once, when the batch is judged; a promise left only here is a promise
nobody will pass again. Writing it into the carrier in the same session is what makes
it a deferral rather than a thought. Closing it is the same move in reverse: batch 04
raised `terrain.hedge_color` as a promotion candidate and dropped it four paragraphs
later, and because both are in the file, the question stays answered.

Batches 01–04 wrote **keep** where this format now says **holds**; the meaning was
always the same, and no batch has ever used the fourth word that once stood here.
