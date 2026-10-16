"""Assignment 2, Parts A and C: the planner and the reviser. This file is yours.

The contract (checks/FORMATS.md, section 10). The checker calls both functions
directly, with a scripted model that keeps a copy of every request, so keep these
exact signatures. You may add keyword arguments with defaults; the checker passes
only these.

    make_plan(client, issue_number, tool_schemas, trace=None) -> dict
    revise_plan(client, plan, step_results, trace=None) -> dict

    client        harness.client.ModelClient: client.create(system=, messages=, tools=)
    issue_number  the issue to triage, an int
    tool_schemas  the tool definitions, as ctx.tools.schemas gives them
    plan          the active plan, its steps' statuses brought up to date
    step_results  one entry per step that has run (section 10.3)
    trace         a harness.trace.TraceWriter, or None. When you get one, trace your
                  model call: trace.model_call(0, response, purpose="plan") in
                  make_plan, purpose="revise" in revise_plan
    returns       a plan (section 10.1): {"meta": {...}, "steps": [...]}

make_plan writes the plan and stops (Lecture 2, slide 15). Its request carries the
task, the issue number and the tool list, and nothing from the issue: not its body,
not its title. The checker looks for each issue's sentinel string in every request.
The usual way to get the plan back as data: offer the model one tool whose
input_schema is agent/plan_schema.json, and read the plan from its call. Mark an
argument that waits on an earlier step's result "$fill"; the executor fills it in.

revise_plan repairs before it replaces: it never changes a step whose status is
done; it may change a step that failed, to retry it, and any step not yet run. A
step whose step result says "done" comes back unchanged, every field but status,
whatever the model wrote. Its request carries structured step results only (status, counts, paths,
error codes): never the issue's text, and never a step result's `args`, which the
executor may have filled in from the issue.

Keep your prompts in prompts/.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "agent" / "plan_schema.json"
PLANNER_PROMPT = "prompts/planner.md"
PLAN_TOOL = "submit_plan"


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def plan_tool() -> dict:
    """The one tool the planner is offered: its input is a plan, in our schema, compiled strict."""
    return {
        "name": PLAN_TOOL,
        "description": "Submit the plan: the steps to run, in order, the last one post_comment.",
        "input_schema": load_schema(),
        "strict": True,
    }


def describe_tools(tool_schemas: list[dict]) -> str:
    """The tool list as text: names, descriptions and argument schemas. Nothing from the issue."""
    return "\n".join(
        f"- {t['name']}: {t.get('description', '')}\n  input schema: {json.dumps(t.get('input_schema', {}))}"
        for t in tool_schemas
    )


def read_plan(response, meta: dict) -> dict:
    """The plan from the planner's submit_plan call, with meta added.

    A reply with no submit_plan call (prose) comes back as a plan with no steps and
    meta.error set, so the way rejects it like any other bad plan."""
    for call in response.tool_calls:
        if call.name == PLAN_TOOL:
            return {"meta": meta, **call.input}
    return {"meta": {**meta, "error": f"no {PLAN_TOOL} call: {response.text[:200]}"}, "steps": []}


def make_plan(client, issue_number: int, tool_schemas: list[dict], trace=None,
              rejected: list[str] | None = None) -> dict:
    """`rejected`, on a re-plan: why the last plan was refused (schema errors, failed
    checks), as lines our own code wrote. Never anything from the issue."""
    system = (ROOT / PLANNER_PROMPT).read_text()
    user = (f"Plan the triage of issue #{issue_number}.\n\n"
            f"The tools the plan may use:\n{describe_tools(tool_schemas)}")
    if rejected:
        user += ("\n\nYour previous plan was rejected, for these reasons. Write a new plan that fixes them:\n"
                 + "\n".join(f"- {r}" for r in rejected))
    response = client.create(system=system, messages=[{"role": "user", "content": user}],
                             tools=[plan_tool()])
    if trace is not None:
        trace.model_call(0, response, purpose="plan")
    meta = {"model": response.model, "prompt": PLANNER_PROMPT,
            "tools": [t["name"] for t in tool_schemas]}
    return read_plan(response, meta)


REVISER_PROMPT = "prompts/reviser.md"
SAFE_RESULT_FIELDS = ("id", "tool", "status", "count", "paths", "error_code")   # never `args`


def _bare(step: dict) -> dict:
    return {k: v for k, v in step.items() if k != "status"}


def revise_plan(client, plan: dict, step_results: list[dict], trace=None, reason: str | None = None) -> dict:
    """The next version of `plan`, repaired from structured step results.

    `reason`, from the checkpoint: why its check failed, in words our code wrote
    from counts and paths. The request carries the plan (whose filled-in arguments
    are still "$fill") and step results without their `args`: no issue text."""
    system = (ROOT / REVISER_PROMPT).read_text()
    safe = [{k: r.get(k) for k in SAFE_RESULT_FIELDS} for r in step_results]
    body = {k: v for k, v in plan.items() if k != "meta"}
    user = (f"The plan, with each step's status:\n{json.dumps(body, indent=1)}\n\n"
            f"The results of the steps that have run:\n{json.dumps(safe, indent=1)}\n\n"
            f"The tools the plan may use: {', '.join((plan.get('meta') or {}).get('tools') or [])}")
    if reason:
        user += f"\n\nWhy the plan is being revised: {reason}"
    response = client.create(system=system, messages=[{"role": "user", "content": user}],
                             tools=[plan_tool()])
    if trace is not None:
        trace.model_call(0, response, purpose="revise")
    meta = {**(plan.get("meta") or {}), "model": response.model, "prompt": REVISER_PROMPT}
    revised = read_plan(response, meta)
    if not isinstance(revised.get("steps"), list) or not revised["steps"]:
        return revised

    # Repair before replace: a step that is done comes back exactly as it was, every
    # field but status, wherever the model put it or whatever it wrote.
    old = {s.get("id"): s for s in plan.get("steps") or []}
    done = [s for s in plan.get("steps") or [] if s.get("status") == "done"]
    done_ids = {s.get("id") for s in done}
    rest = [s for s in revised["steps"] if isinstance(s, dict) and s.get("id") not in done_ids]
    for step in rest:
        before = old.get(step.get("id"))
        if before is not None and before.get("status") == "failed" and _bare(before) == _bare(step):
            step["status"] = "failed"          # unchanged, so not retried
        else:
            step["status"] = "pending"         # new, changed, or not yet run
    revised["steps"] = [copy.deepcopy(s) for s in done] + rest
    return revised
