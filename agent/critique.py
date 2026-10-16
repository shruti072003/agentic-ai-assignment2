"""Assignment 2, Part D: the second pass before posting. This file is yours.

Two ways to read a drafted comment before it is posted (Lecture 2, slides 31 to 34).
The checker runs your planned way in each mode against scripted models, so keep
these exact signatures (checks/FORMATS.md, section 10.2). You may add keyword
arguments with defaults; the checker passes only these.

    verify(draft, ctx) -> {"passed": bool, "reasons": [str, ...]}

        The external mode: checks in code, against the repository and this run's
        own record. Four checks:
          1. the draft parses: a `label:` line with a known label, and a `file:`
             line with a path or none (section 1; harness.formats.parse_triage_comment);
          2. a path it names exists in the repository (ctx.root / "fixtures" / "repo");
          3. this run saw that path: a search_repo result or a read_file call in this
             run's trace returned it. Each tool_call line's `paths` field lists the
             files it returned; read the trace so far with
             harness.trace.read_trace(ctx.trace.path);
          4. the label and the file agree: a question names none, and every other
             label names a file.
        "reasons" says why each failed check failed; it is empty when all pass.
        verify reads only what the run itself can see: never the answer key, and
        never Assignment 1's grounded check in eval/. The checker flags a second
        pass that does.

    self_critique(client, draft, evidence, trace=None) -> {"decision": "accept" | "revise", "reason": str | None}

        The self mode: one model call that rereads the draft, given the issue and
        what the run's steps returned (`evidence`, a dict of your design), and
        accepts it or asks for a revision with a reason. Given a trace, trace the
        call: trace.model_call(n, response, purpose="critique").

    draft   the comment your executor drafted, the text post_comment would post
    ctx     the run's harness.runner.WayContext
    client  harness.client.ModelClient

The loop that uses them, its rewrite call and MAX_ROUNDS live in ways/planned.py.
Keep your prompts in prompts/.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.formats import LABELS, parse_triage_comment
from harness.trace import read_trace

ROOT = Path(__file__).resolve().parent.parent
CRITIC_PROMPT = "prompts/critic.md"

VERDICT_TOOL = {
    "name": "verdict",
    "description": "Accept the draft comment as it is, or ask for a revision and say why.",
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["accept", "revise"]},
            "reason": {"type": ["string", "null"], "description": "Why it must change; null on accept."},
        },
        "required": ["decision", "reason"],
        "additionalProperties": False,
    },
    "strict": True,
}


def seen_paths(ctx) -> list[str]:
    """Every path this run's tool calls returned so far, in order: its own record."""
    seen = []
    for line in read_trace(ctx.trace.path):
        if line.get("type") == "tool_call":
            seen += [p for p in line.get("paths") or [] if p not in seen]
    return seen


def verify(draft: str, ctx) -> dict:
    label, file = parse_triage_comment(draft)
    reasons = []
    # 1. It parses: a known label, and a file line with a path or none.
    if label not in LABELS:
        reasons.append(f"parses: the label line must be one of {', '.join(LABELS)}, not {label!r}")
    if file is None:
        reasons.append("parses: there is no 'file:' line; give a path or none")
    if file not in (None, "none"):
        # 2. The path exists in the repository.
        if not (Path(ctx.root) / "fixtures" / "repo" / file).is_file():
            reasons.append(f"exists: {file} is not a file in the repository")
        # 3. This run saw it: a search returned it or a read read it.
        elif file not in seen_paths(ctx):
            reasons.append(f"seen: no search or read in this run returned {file}")
    # 4. The label and the file agree.
    if label == "question" and file not in (None, "none"):
        reasons.append("agree: a question names no file (file: none)")
    if label in LABELS and label != "question" and file == "none":
        reasons.append(f"agree: a {label} must name the file it concerns, not none")
    return {"passed": not reasons, "reasons": reasons}


def self_critique(client, draft: str, evidence: dict, trace=None, step: int = 0) -> dict:
    system = (ROOT / CRITIC_PROMPT).read_text()
    user = (f"The issue:\n{evidence.get('issue') or '(not read)'}\n\n"
            f"What the run found, step by step:\n{json.dumps(evidence.get('steps') or [], indent=1)}\n\n"
            f"The draft to check:\n{draft}")
    response = client.create(system=system, messages=[{"role": "user", "content": user}], tools=[VERDICT_TOOL])
    if trace is not None:
        trace.model_call(step, response, purpose="critique")
    for call in response.tool_calls:
        if call.name == VERDICT_TOOL["name"] and call.input.get("decision") in ("accept", "revise"):
            decision = call.input["decision"]
            reason = call.input.get("reason") if decision == "revise" else None
            return {"decision": decision, "reason": reason or ("revise" if decision == "revise" else None)}
    # No verdict: an unreadable critique is not an accept.
    return {"decision": "revise", "reason": "the critic gave no verdict: " + (response.text[:200] or "empty reply")}
