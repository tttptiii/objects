# Judgment records

One file per reviewed batch: `YYYY-MM-DD-batch-NN.md`. The author's verdicts are the
only thing that revises the rules, so they are part of the record — the loop is
rules → pieces → judgment → next revision of the rules, and this directory is the
third arrow.

Format, one table per batch:

| piece | verdict | note |
|---|---|---|
| 012 | keep | — |
| 013 | kill | the reason, in the author's words where possible |
| 014 | revise | what was changed in response, and where |

Verdicts: **keep** (accepted as part of the series), **kill** (rejected — the piece
stays on disk as a record, the reason feeds the rules), **revise** (accepted after a
recorded spec or rules change). Below the table, note any rules revision the batch
triggered, with the resulting `rules_sha`.
