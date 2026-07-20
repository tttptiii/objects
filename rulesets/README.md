# Rule sets

A rule set is **a prompt plus its validation config**, handled as one unit. This project
revises its rules constantly, so the rules live in data rather than code — trying a new
one means copying a directory, not editing Python.

```
rulesets/{name}/
  rules.md      # the prompt sent to the model — this and nothing else
  checks.json   # validation config (a partial override of DEFAULTS in scripts/ruleset.py)
  notes.md      # for humans: revision history and where each rule came from
```

## What exists

| rule set | what it does |
|----------|--------------|
| `pylon-series` | A line of lattice transmission towers in fog — camera, line rhythm, palette, and an optional low-poly landscape, sampled per piece against fixed tower geometry |

## Running one

```sh
python scripts/sample_pylons.py --only 0,1,2          # sample specs (direction fixed per piece number)
python scripts/render_pylons.py --pieces 0-2          # draft renders
python scripts/render_pylons.py --pieces 2 --full     # full resolution
```

Specs land in `scenes/{ruleset}/` and images in `outputs/{ruleset}/`. Candidates the
validator refused are kept too — `scenes/{ruleset}/rejected/` with the violations
recorded in each file. What the rules refuse is as much a part of the procedure as what
they pass.

## Changing the rules

Edit `rules.md` (the prompt) and `checks.json` (the thresholds). Touch code only when
adding a new kind of check:

1. add the config key to the ruleset's `checks.json` (inside the `pylon` dict)
2. add the check to `scripts/validate_pylon.py`
3. record why in `notes.md`

Every sampled spec stores `meta.rules_sha`, a content hash of the exact `rules.md`
revision that produced it.

## Thresholds come from judgment against measurement

Almost every threshold was set by comparing the author's judgment of rendered batches
against measurements of the same specs — the fog-clear zone from "the subject must stay
sharp", the anchor distance and frame-share bands from "the tower must command the frame
with air around it", the far-dissolve ceiling from "every tower stays countable". Each
revision and its reason is recorded in `notes.md`.
