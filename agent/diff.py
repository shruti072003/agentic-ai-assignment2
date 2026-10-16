"""Assignment 2, Part E: the diff of the plan against what ran. This file is yours.

"The one people skip" (Lecture 2, slide 54). The checker calls it on slide 51's run
in fixtures/traces/s49/, so keep this exact signature (checks/FORMATS.md, section
10.2):

    diff(plan, trace) -> {"steps": {step_id: label}, "extra": [step numbers]}

    steps   every plan step, labelled
              executed   it ran, in plan order
              skipped    it never ran
              reordered  it ran, out of plan order
    extra   the step numbers of tool_call lines that match no plan step

How a call matches a step: a tool_call line that names a plan_step belongs to that
step if it calls that step's tool (every retry of a step is that step's). A line
with no plan_step (the transcribed runs) is matched to the earliest plan step not
yet matched whose tool it calls and whose fixed arguments it carries: every
argument the plan writes other than "$fill" must have the same value in the call.
Each such step takes one call, so a second identical call is extra.
"""

from __future__ import annotations

import json

FILL = "$fill"


def _args(step: dict) -> dict:
    if "args_json" in step:
        try:
            return json.loads(step["args_json"]) or {}
        except (TypeError, json.JSONDecodeError):
            return {}
    return step.get("args") or {}


def _fits(step: dict, call: dict) -> bool:
    if step.get("tool") != call.get("name"):
        return False
    given = call.get("input") or {}
    for key, value in _args(step).items():
        if isinstance(value, str) and FILL in value:
            continue
        if given.get(key) != value:
            return False
    return True


def match(plan: dict, trace: list[dict]) -> tuple[dict, list]:
    """{step id: [tool_call lines]} for the steps that ran, and the lines that match none."""
    steps = plan.get("steps") or []
    by_id = {str(s.get("id")): s for s in steps}
    matched = {str(s.get("id")): [] for s in steps}
    extra = []
    for line in (x for x in trace if x.get("type") == "tool_call"):
        ps = line.get("plan_step")
        if ps is not None:
            step = by_id.get(str(ps))
            if step is not None and step.get("tool") == line.get("name"):
                matched[str(ps)].append(line)
            else:
                extra.append(line)
            continue
        step = next((s for s in steps if not matched[str(s.get("id"))] and _fits(s, line)), None)
        if step is None:
            extra.append(line)
        else:
            matched[str(step.get("id"))].append(line)
    return matched, extra


def diff(plan: dict, trace: list[dict]) -> dict:
    matched, extra = match(plan, trace)
    order = {id(line): i for i, line in enumerate(trace)}
    labels, latest = {}, -1
    for step in plan.get("steps") or []:
        lines = matched[str(step.get("id"))]
        if not lines:
            labels[step.get("id")] = "skipped"
            continue
        at = order[id(lines[0])]
        labels[step.get("id")] = "executed" if at > latest else "reordered"
        latest = max(latest, at)
    return {"steps": labels, "extra": [line.get("step") for line in extra]}
