"""Assignment 2, Part E: three detectors, and Lecture 2's ladder. This file is yours.

Three of the four plan failures have a detector of a few lines over a trace
(Lecture 2, slides 45 to 49); the validator is the fourth. The checker calls these
on fixtures/traces/, so keep these exact signatures (checks/FORMATS.md, section
10.2):

    repeat_check(trace) -> list[list]
        Circular reasoning. Exact repeats: two tool_call lines with the same tool
        and the same arguments. Returns each group of repeats as the step numbers
        of its tool_call lines, e.g. [[1, 3], [2, 4]].

    orphan_check(plan) -> list
        Hallucinated steps. The ids of steps, other than the last, whose result no
        later step uses: no later step lists them in depends_on.

    expected_result_check(plan, trace) -> list
        Premature stopping, in part. The ids of plan steps that did not run and
        return their result. It catches skipped steps, not a plan too short.

    ladder(plan, trace) -> {"q1": {"fired": bool, "ids": [...]}, "q2": ..., "q3": ...}
        Lecture 2's questions 1 to 3 (slide 50), in order: did any two steps do
        the same thing; is any step's result never used; did every planned step
        execute. "ids" says what fired: groups of step numbers for q1, step ids
        (or, from a trace, step numbers) for q2 and q3. Question 4 needs
        judgment, so it goes in your report, not here.

    plan    a plan (section 10.1)
    trace   a list of trace lines, as harness.trace.read_trace returns them

A plan with no depends_on cannot answer question 2 from the plan. Here it is then
answered from the trace, as a judgment: a call whose result the run fetched again
later, with the same tool and the same arguments, was not used (had it been, there
was no reason to fetch it again).
"""

from __future__ import annotations

import json

from agent.diff import match


def _key(line: dict) -> str:
    return json.dumps([line.get("name"), line.get("input") or {}], sort_keys=True)


def repeat_check(trace: list[dict]) -> list[list]:
    groups: dict[str, list] = {}
    for line in trace:
        if line.get("type") == "tool_call":
            groups.setdefault(_key(line), []).append(line.get("step"))
    return [steps for steps in groups.values() if len(steps) > 1]


def orphan_check(plan: dict) -> list:
    steps = plan.get("steps") or []
    orphans = []
    for i, step in enumerate(steps[:-1]):
        later = steps[i + 1:]
        if not any(step.get("id") in (s.get("depends_on") or []) for s in later):
            orphans.append(step.get("id"))
    return orphans


def expected_result_check(plan: dict, trace: list[dict]) -> list:
    matched, _ = match(plan, trace)
    missing = []
    for step in plan.get("steps") or []:
        lines = matched[str(step.get("id"))]
        if not lines or all(line.get("is_error") for line in lines):
            missing.append(step.get("id"))
    return missing


def unused_from_trace(trace: list[dict]) -> list:
    """Question 2 with no depends_on: the tool_call steps whose result an exact repeat
    fetched again later."""
    calls = [x for x in trace if x.get("type") == "tool_call"]
    return [c.get("step") for i, c in enumerate(calls) if any(_key(c) == _key(d) for d in calls[i + 1:])]


def ladder(plan: dict, trace: list[dict]) -> dict:
    repeats = repeat_check(trace)
    has_deps = any("depends_on" in s for s in plan.get("steps") or [])
    unused = orphan_check(plan) if has_deps else unused_from_trace(trace)
    skipped = expected_result_check(plan, trace)
    return {"q1": {"fired": bool(repeats), "ids": repeats},
            "q2": {"fired": bool(unused), "ids": unused},
            "q3": {"fired": bool(skipped), "ids": skipped}}


if __name__ == "__main__":
    from pathlib import Path

    from agent.diff import diff
    from harness.trace import read_trace

    root = Path(__file__).resolve().parent.parent / "fixtures" / "traces"
    for run in ("looping", "s49"):
        plan = json.loads((root / run / "plan.json").read_text())
        trace = read_trace(root / run / "trace.jsonl")
        print(run, json.dumps(ladder(plan, trace)))
        print(run, "diff", json.dumps(diff(plan, trace)))
