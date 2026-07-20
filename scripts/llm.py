"""Headless claude CLI calls — the one place the pipeline talks to a model.

No API key is involved: the installed `claude` CLI is invoked with `-p`, so a logged-in
session is all that is needed.
"""

import json
import re
import shutil
import subprocess
import sys

DEFAULT_MODEL = "claude-sonnet-5"

# Seconds — so a call hanging without a response does not hold the batch up. It has to be
# generous: measured generation of one scene from ~10K characters of rules ran ~280s at
# default effort.
CALL_TIMEOUT = 600

# Effort dominates latency. Measured on the same scene generation (no concurrency):
# low 47s / default 549s — an 11x difference with comparable results. The rules are explicit
# and the output is structured JSON, so long reasoning buys nothing.
DEFAULT_EFFORT = "low"


def call_claude(prompt, model, timeout=CALL_TIMEOUT, session=None, resume=False,
                effort=DEFAULT_EFFORT, allowed_tools=None):
    """One headless call.

    Passing a session (UUID) saves the conversation under that session, and resume=True
    continues it. Retries then need only send the violations — a few hundred characters —
    instead of resending the full rules, since the model already knows what it just produced.

    allowed_tools opens named tools to the call — e.g. ["Read"] lets the model open a
    rendered image and look at the current state of a composition before deciding.
    """
    exe = shutil.which("claude")
    if not exe:
        sys.exit("claude CLI not found")
    if resume:
        args = ["--resume", session]  # on resume the model is already attached to the session
    else:
        args = ["--model", model] + (["--session-id", session] if session else [])
    if effort:
        args += ["--effort", effort]
    if allowed_tools:
        args += ["--allowedTools", ",".join(allowed_tools)]
    try:
        r = subprocess.run(
            [exe, "-p", *args, "--strict-mcp-config"],
            input=prompt, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        # Without a timeout, one stalled call holds the whole batch indefinitely
        # (measured: 10min+)
        raise RuntimeError(f"claude CLI did not respond (over {timeout}s)")
    if r.returncode != 0:
        raise RuntimeError(f"claude CLI failed (exit {r.returncode}): {r.stderr[:500]}")
    return r.stdout.strip()


def extract_json(text):
    """Parse the first JSON value (object or array) in the response."""
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise ValueError("no JSON in the response")
    decoder = json.JSONDecoder()
    value, _ = decoder.raw_decode(text[start:])
    return value
