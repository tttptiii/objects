"""Rule sets — a prompt and its validation config, handled as one unit.

This project revises its rules constantly. If every experiment meant editing code, the
experiments would slow down and reverting to an earlier rule set would be painful. So
**what to check and at what threshold lives in data**, and only the checking logic is code.

    rulesets/
      pylon-series/
        rules.md      # the prompt sent to the model — this and nothing else
        checks.json   # validation config, layered over DEFAULTS below
        notes.md      # for humans: version metadata, revision history, rationale

`checks.json` is a **partial override** of DEFAULTS. Set a value to null to disable that
check entirely. An unknown key raises at load time — a typo silently disabling a check
would be worse than a crash.
"""

import hashlib
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULESETS_DIR = os.path.join(ROOT, "rulesets")

# Defaults for every rule set; checks.json overrides only what it needs.
DEFAULTS = {
    # Pylon series (validate_pylon.py) — parameter ranges and camera/contrast rules,
    # as one dict.
    "pylon": None,
}


class RuleSet:
    def __init__(self, name, rules, checks, directory):
        self.name = name
        self.rules = rules
        self.checks = checks
        self.directory = directory

    def __getitem__(self, key):
        return self.checks[key]

    def get(self, key, default=None):
        return self.checks.get(key, default)

    @property
    def sha(self):
        """Short content hash of the prompt — stored in each sampled spec's meta, so
        every picture records exactly which revision of the rules produced it."""
        return hashlib.sha256(self.rules.encode("utf-8")).hexdigest()[:12]


def load(name):
    d = os.path.join(RULESETS_DIR, name)
    rules_path = os.path.join(d, "rules.md")
    if not os.path.exists(rules_path):
        avail = sorted(os.listdir(RULESETS_DIR)) if os.path.isdir(RULESETS_DIR) else []
        raise FileNotFoundError(
            f"no rule set '{name}' at {rules_path}. Available: {', '.join(avail) or 'none'}")
    with open(rules_path, encoding="utf-8") as f:
        rules = f.read()
    checks = dict(DEFAULTS)
    checks_path = os.path.join(d, "checks.json")
    if os.path.exists(checks_path):
        with open(checks_path, encoding="utf-8") as f:
            override = json.load(f)
        unknown = set(override) - set(DEFAULTS)
        if unknown:
            raise ValueError(f"{checks_path}: unknown config keys {sorted(unknown)}")
        checks.update(override)
    return RuleSet(name, rules, checks, d)
