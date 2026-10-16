"""Way 5, planned: RepoMind with a plan it cannot quietly abandon. This file is yours.

Assignment 2, Parts B, C and D. Like every way, run(ctx) -> None (see ways/__init__.py):

    python run.py --way planned --set dev
    python run.py --way planned --critique self --set dev      # or external; off is the default

The run, in order (checks/FORMATS.md, section 10):

  1. Plan: agent.plan.make_plan(ctx.client, ctx.issue_number, ctx.tools.schemas,
     trace=ctx.trace). The planner never sees the issue.
  2. Check, then validate. First the plan against agent/plan_schema.json, with
     harness.schema.errors (a vendor may accept "strict" and not enforce it), and
     that its last step is post_comment (a plan without one passes the four checks
     and posts nothing); a plan that breaks either writes
     ctx.trace.event("schema", errors=...). Then agent.validator.validate(plan). A
     plan that fails any of these is rejected and re-planned, at most MAX_REPLANS
     times; then the run escalates: ctx.trace.end("escalated", detail), no tool
     call, nothing posted.
  3. Log: ctx.trace.plan(1, plan), before the first tool call.
  4. Execute the steps in order, each tool call through ctx.call_tool, naming the
     plan step it runs for: ctx.call_tool(n, tool, args, plan_step=step["id"]). Where a step
     has arguments marked "$fill", one model call fills them in: offer it that
     step's tool alone, and trace it with ctx.trace.model_call(n,
     response, purpose="execute"). Act on the tool calls a response holds,
     whatever its stop_reason says. The executor may run, retry or halt a step; it
     may not add, drop or reorder one. A call that is not the current step's tool
     never runs: refuse it and write ctx.trace.event("off_plan", step=..., tool=...).
  5. Part C: revise only at one checkpoint, after a search step: the first search
     step, and any search step a revision added or changed, until MAX_REVISIONS is
     spent; and only when the check there fails (a check in code, not in a prompt,
     such as "the search returned at least one source file, not only documentation
     or translations"): revise_plan, which never changes a step that is done but may
     change a failed one to retry it, then check the new version against the schema
     and validate it, write
     ctx.trace.event("revision", checkpoint=..., version=..., valid=...), and log a
     valid one with ctx.trace.plan(version, plan). A revision that fails the
     validator never runs, and counts against the bound.
  6. Part D: before post_comment, the second pass, in the mode ctx.critique names
     (off, self or external), at most MAX_ROUNDS rounds. A round checks the
     current draft (agent.critique.verify, or agent.critique.self_critique) and
     writes ctx.trace.event("critique", round=..., mode=..., result="pass" or
     "fail", reason=..., changed=..., draft=...). A draft that fails, with a round
     left, is rewritten by one model call given the reason, traced with
     purpose="critique", and the next round checks the new draft. A draft still
     failing in the last round is held: not posted, and the run ends escalated.
     Nothing is posted until the last round, and a run posts one comment at most.

The comment is Assignment 1's: two lines, label: and file: (section 1). A run that
finishes its plan can leave its ending to the runner, which writes completed.

Part G's ways/notes.py extends this way without changing it, so build this one with
hooks it can override: for example an executor class whose methods run after every
step, build what each model call sees, and run before the comment.

The checker reads MAX_REPLANS, MAX_REVISIONS and MAX_ROUNDS from this module, and
runs this way against scripted models (harness/scripted.py). You can run them too,
free:

    python run.py --way planned --backend harness.scripted:AlwaysInvalidPlanner --set dev
    python run.py --way planned --backend harness.scripted:OffPlanExecutor --set dev
    python run.py --way planned --critique external --backend harness.scripted:DraftNamesUnseenFile --set dev
"""

from __future__ import annotations

import json

from agent.critique import self_critique, verify
from agent.plan import load_schema, make_plan, revise_plan
from agent.validator import validate
from harness.formats import FILL, format_triage_comment, normalize_path, parse_triage_comment, result_paths
from harness.schema import errors as schema_errors

MAX_REPLANS = 2        # Part B: re-plans after a rejected plan before you escalate; say why in your report
MAX_REVISIONS = 2      # Part C: revisions a run (at least 1), accepted and rejected alike; say why in your report
MAX_ROUNDS = 2         # Part D: second-pass rounds a run, 1 or 2, the same in both modes

MAX_ATTEMPTS = 2       # tries at one step whose arguments the model fills in: the first, and one retry
ISSUE_TOOLS = ("read_issue", "post_comment")   # tools whose `number` must be this run's issue
CHECKPOINT = "after_search"
SEARCH_CAP, SEARCH_DEFAULT = 20, 10   # search_repo's hard cap on hits, and its default
EVIDENCE_CHARS = 4000  # how much of one step's result the second pass sees


# -- reading a plan ------------------------------------------------------------------

def step_args(step: dict) -> dict:
    """A step's arguments as a dict, from args_json (our schema) or args (the fixtures)."""
    if "args_json" in step:
        return json.loads(step["args_json"])
    return dict(step.get("args") or {})


def is_fill(value) -> bool:
    """An argument the executor fills in. The planner sometimes writes "$fill" inside a
    template ("label: $fill\\nfile: $fill"); a value with no fixed meaning is filled whole."""
    return isinstance(value, str) and FILL in value


def plan_errors(plan: dict) -> list[str]:
    """Everything that makes a plan unrunnable before the four checks: the schema, an
    args_json that is not a JSON object, and the rule that it ends with its one post_comment."""
    if (plan.get("meta") or {}).get("error"):
        return [plan["meta"]["error"]]
    body = {k: v for k, v in plan.items() if k != "meta"}
    found = schema_errors(body, load_schema())
    if found:
        return found
    steps = plan["steps"]
    for i, step in enumerate(steps):
        try:
            if not isinstance(step_args(step), dict):
                found.append(f"steps[{i}].args_json: not a JSON object")
        except json.JSONDecodeError as exc:
            found.append(f"steps[{i}].args_json: not valid JSON ({exc.msg})")
    if not steps or steps[-1].get("tool") != "post_comment":
        found.append("the last step is not post_comment")
    if sum(1 for s in steps if s.get("tool") == "post_comment") > 1:
        found.append("more than one post_comment step: a run posts one comment")
    return found


def is_source(path: str) -> bool:
    """Source code: a .ts or .tsx file under src/. Not docs, changelogs, translations or tests."""
    return path.startswith("src/") and path.endswith((".ts", ".tsx"))


def search_check(record: dict) -> str | None:
    """The checkpoint's check, in code: None when it passes, else why it failed, in
    words built from counts and paths only (the reviser sees this; never a query)."""
    if record["status"] != "done":
        return f"step {record['id']} (search_repo) failed: {record['error_code']}, {record['count'] or 0} results"
    if not any(is_source(p) for p in record["paths"]):
        return (f"step {record['id']} (search_repo) returned {record['count']} hits, none of them in a source "
                f"file (.ts or .tsx under src/): only {', '.join(record['paths'][:6])}")
    limit = int((record["args"] or {}).get("max_results", SEARCH_DEFAULT))
    if record["count"] >= min(limit, SEARCH_CAP):
        return (f"step {record['id']} (search_repo) was cut off at {record['count']} hits, in path order, "
                f"so the term is too common to point anywhere: it matched {len(record['paths'])} files, "
                f"first {', '.join(record['paths'][:4])}")
    return None


def explain_failures(failures: list[dict]) -> list[str]:
    what = {1: "names a tool that is not in the tool list",
            2: "depends on a step that is not before it",
            3: "has no tool or no expected_result",
            4: "the plan has no steps, or more than the step limit"}
    return [f"step {f['step']}: {what[f['check']]}" if f["step"] is not None else what[4] for f in failures]


# -- the executor ----------------------------------------------------------------------

class Escalate(Exception):
    """The run hands the issue to a person: trace.end("escalated", detail), nothing posted."""


class Executor:
    """Plan, validate, log, then run the plan's steps in order and nothing else.

    Hooks a subclass (ways/notes.py) overrides, each a no-op or a default here:
      history(step)          what the fill call sees besides the step itself
      extra_context()        text added to every executor call
      after_step(step, rec)  runs after every step, done or failed
      before_draft()         runs just before the comment is drafted
      before_end(reason)     runs once, just before the run ends, however it ends
    and two that Parts C and D fill in:
      checkpoint(step, rec)  after a search step: may revise the plan
      second_pass(draft)     before posting: the draft to post, or None to hold it
    """

    prompt_file = "executor.md"

    def __init__(self, ctx):
        self.ctx = ctx
        self.trace = ctx.trace
        self.tools = {t["name"]: t for t in ctx.tools.schemas}
        self.n = 0                    # the step number of the most recent model call
        self.plan = None
        self.version = 0
        self.results = {}             # step id -> {"tool", "input", "content", "is_error"}
        self.step_results = []        # checks/FORMATS.md, section 10.3
        self.calls_made = set()       # (tool, input) already run: never run one twice
        self.ended = False
        self.search_paths = []        # every path a search returned, in order
        self.revisions = 0            # revision attempts, accepted and rejected alike
        self.watch = set()            # search step ids the checkpoint checks after they run

    # -- the run -----------------------------------------------------------------------
    def run(self) -> None:
        try:
            self.plan = self.make_valid_plan()
            self.version = 1
            self.trace.plan(1, self.plan)
            first = next((s for s in self.plan["steps"] if s["tool"] == "search_repo"), None)
            self.watch = {first["id"]} if first else set()
            while (step := self.next_step()) is not None:
                self.run_step(step)
        except Escalate as exc:
            self.finish("escalated", str(exc))
            return
        self.finish("completed")

    def finish(self, reason: str, detail: str = "") -> None:
        if self.ended:
            return
        self.ended = True
        self.before_end(reason)
        if reason == "escalated":
            self.trace.end("escalated", detail)
        # A finished plan leaves its ending to the runner, which writes completed.

    # -- planning: the reject exit ----------------------------------------------------
    def make_valid_plan(self) -> dict:
        rejected = None
        for attempt in range(MAX_REPLANS + 1):
            plan = make_plan(self.ctx.client, self.ctx.issue_number, self.ctx.tools.schemas,
                             trace=self.trace, rejected=rejected)
            problems = plan_errors(plan)
            if problems:
                self.trace.event("schema", errors=problems, attempt=attempt + 1)
                rejected = problems
                continue
            result = validate(plan)
            if not result["valid"]:
                self.trace.event("plan_rejected", attempt=attempt + 1, failures=result["failures"])
                rejected = explain_failures(result["failures"])
                continue
            return plan
        raise Escalate(f"no valid plan after {MAX_REPLANS + 1} planner calls")

    def next_step(self):
        return next((s for s in self.plan["steps"] if s.get("status", "pending") == "pending"), None)

    # -- one step ----------------------------------------------------------------------
    def run_step(self, step: dict) -> None:
        sid, tool = step["id"], step["tool"]
        args = step_args(step)
        fills = [k for k, v in args.items() if is_fill(v)]
        if tool == "post_comment":
            self.before_draft()
        attempts = MAX_ATTEMPTS if fills else 1
        problem, called = None, None     # called: the record of this step's latest tool call
        for _ in range(attempts):
            tool_input, problem = self.fill(step, args, fills, problem) if fills else (args, None)
            if problem is None:
                problem = self.check_input(tool, tool_input)
            if problem is None and tool == "post_comment":
                draft = tool_input.get("body", "")
                final = self.second_pass(draft)
                if final is None:
                    raise Escalate("the comment draft still failed the second pass at its bound")
                tool_input = {**tool_input, "body": final}
            if problem is None:
                called = self.call(step, tool_input)
                if called["status"] == "done":
                    break
                problem = f"{called['error_code']}: {json.dumps(tool_input)} returned {called['content'][:200]!r}"
        record = called if called is not None else self.failed_record(step, problem)
        if called is not None and called["status"] == "done":
            problem = None
        step["status"] = record["status"]
        self.after_step(step, record)
        revised = False
        if tool == "search_repo" and called is not None:   # only after a search call, the step's last tool call
            revised = self.checkpoint(step, record)
        if record["status"] == "failed":
            # Only a revision that changed the failed step (now pending again) or replaced
            # it lets the run go on; otherwise a later step would run without it.
            now = next((s for s in self.plan["steps"] if s["id"] == sid), None)
            if not revised or (now is not None and now.get("status") != "pending"):
                raise Escalate(f"step {sid} ({tool}) could not run: {problem}")

    def fill(self, step: dict, args: dict, fills: list[str], problem):
        """One model call fills the step's "$fill" arguments, offered that step's tool alone.
        A call to any other tool is refused, never runs, and is written as off_plan."""
        self.n += 1
        response = self.ctx.client.create(system=self.ctx.prompt(self.prompt_file),
                                          messages=[{"role": "user", "content": self.fill_request(step, args, fills, problem)}],
                                          tools=[self.tools[step["tool"]]])
        self.trace.model_call(self.n, response, purpose="execute")
        chosen = None
        for call in response.tool_calls:      # acted on whatever stop_reason says
            if call.name != step["tool"]:
                self.trace.event("off_plan", step=step["id"], tool=call.name, input=call.input)
            elif chosen is None:
                chosen = call
        if chosen is None:
            return None, (f"off_plan: you called {response.tool_calls[0].name}, not {step['tool']}"
                          if response.tool_calls else "no_call: you did not call the tool")
        filled = dict(args)
        for key in fills:
            value = chosen.input.get(key)
            if value is None or (isinstance(value, str) and (not value.strip() or FILL in value)):
                return None, f"unfilled: you did not fill in {key}"
            filled[key] = value
        return filled, None

    def fill_request(self, step: dict, args: dict, fills: list[str], problem) -> str:
        fixed = {k: v for k, v in args.items() if k not in fills}
        parts = [f"Issue #{self.ctx.issue_number}."]
        parts += self.history(step)
        parts.append(f"The step to run now, step {step['id']}: {step['action']}\n"
                     f"Expected result: {step['expected_result']}\n"
                     f"Tool: {step['tool']}. Fixed arguments, keep them as they are: {json.dumps(fixed)}\n"
                     f"Arguments to fill in: {', '.join(fills)}")
        if problem:
            parts.append(f"Your last try at this step did not work: {problem}. Try something different.")
        extra = self.extra_context()
        if extra:
            parts.append(extra)
        return "\n\n".join(parts)

    def check_input(self, tool: str, tool_input: dict):
        """Check filled-in arguments against the named tool's schema before the call."""
        found = schema_errors(tool_input, self.tools[tool]["input_schema"])
        if found:
            return "bad_args: " + "; ".join(found)
        if tool in ISSUE_TOOLS and tool_input.get("number") != self.ctx.issue_number:
            return f"bad_args: number must be this issue, {self.ctx.issue_number}"
        if tool == "read_file" and normalize_path(str(tool_input.get("path"))) not in self.seen():
            return (f"bad_args: {tool_input.get('path')} was not returned by any search in this run; "
                    f"read one of: {', '.join(self.seen()[:20])}")
        if (tool, json.dumps(tool_input, sort_keys=True)) in self.calls_made:
            return f"repeat: {tool} {json.dumps(tool_input)} already ran in this run; choose different arguments"
        return None

    def seen(self) -> list[str]:
        """Every path this run's searches returned so far, in order."""
        return self.search_paths

    def call(self, step: dict, tool_input: dict) -> dict:
        tool = step["tool"]
        self.calls_made.add((tool, json.dumps(tool_input, sort_keys=True)))
        result = self.ctx.call_tool(self.n, tool, tool_input, plan_step=step["id"])
        paths = result_paths(tool, tool_input, result.content, result.is_error)
        if tool == "search_repo":
            self.search_paths += [p for p in paths if p not in self.search_paths]
        hits = [l for l in result.content.splitlines() if l.strip()] if paths else []
        count = len(hits) if tool == "search_repo" else None
        if result.is_error:
            code = "not_found" if "no such file" in result.content or "not found" in result.content else "tool_error"
        elif tool == "search_repo" and not paths:
            code = "no_matches"
        else:
            code = None
        record = {"id": step["id"], "tool": tool, "status": "failed" if code else "done", "count": count,
                  "paths": paths, "error_code": code, "args": tool_input, "content": result.content}
        self.results[step["id"]] = record
        self.record(record)
        return record

    def failed_record(self, step: dict, problem: str) -> dict:
        record = {"id": step["id"], "tool": step["tool"], "status": "failed", "count": None, "paths": [],
                  "error_code": (problem or "failed").split(":")[0], "args": None, "content": problem or ""}
        self.record(record)
        return record

    def record(self, record: dict) -> None:
        """One entry per step that has run, latest attempt, in the order they ran (section 10.3)."""
        entry = {k: record[k] for k in ("id", "tool", "status", "count", "paths", "error_code", "args")}
        self.step_results = [r for r in self.step_results if r["id"] != record["id"]] + [entry]

    # -- hooks -------------------------------------------------------------------------
    def history(self, step: dict) -> list[str]:
        """The issue, and the results of the steps this one depends on, directly or not:
        what the plan says the step uses."""
        by_id = {s["id"]: s for s in self.plan["steps"]}
        needed, todo = set(), list(step.get("depends_on") or [])
        while todo:
            sid = todo.pop()
            if sid not in needed and sid in by_id:
                needed.add(sid)
                todo += by_id[sid].get("depends_on") or []
        parts = []
        for sid, rec in self.results.items():
            if rec["tool"] == "read_issue" and rec["status"] == "done":
                parts.append(f"The issue, as read_issue returned it:\n{rec['content']}")
        for sid, rec in self.results.items():
            if sid in needed and rec["tool"] != "read_issue":
                parts.append(f"Result of step {sid} ({rec['tool']} {json.dumps(rec['args'])}):\n{rec['content']}")
        return parts

    def extra_context(self) -> str:
        return ""

    def after_step(self, step: dict, record: dict) -> None:
        pass

    def before_draft(self) -> None:
        pass

    def before_end(self, reason: str) -> None:
        pass

    # -- Part C: the one checkpoint ---------------------------------------------------
    def checkpoint(self, step: dict, record: dict) -> bool:
        """After the first search, and after any search a revision added or changed: check
        in code, and revise only if the check fails and the bound allows. True when a
        valid revision was adopted."""
        if step["id"] not in self.watch:
            return False
        self.watch.discard(step["id"])
        reason = search_check(record)
        if reason is None or self.revisions >= MAX_REVISIONS:
            return False
        self.revisions += 1
        version = self.version + 1
        new = revise_plan(self.ctx.client, self.plan, self.step_results, trace=self.trace, reason=reason)
        problems = plan_errors(new)
        failures = [] if problems else validate(new)["failures"]
        valid = not problems and not failures
        self.trace.event("revision", checkpoint=CHECKPOINT, version=version, valid=valid,
                         reason=reason, errors=problems, failures=failures)
        if not valid:
            return False
        old = {s["id"]: {k: v for k, v in s.items() if k != "status"} for s in self.plan["steps"]}
        self.watch |= {s["id"] for s in new["steps"] if s["tool"] == "search_repo" and s["status"] == "pending"
                       and old.get(s["id"]) != {k: v for k, v in s.items() if k != "status"}}
        self.plan, self.version = new, version
        self.trace.plan(version, new)
        return True

    # -- Part D: the second pass --------------------------------------------------------
    def mode(self) -> str:
        return self.ctx.critique

    def second_pass(self, draft: str):
        """At most MAX_ROUNDS rounds; the draft to post, or None to hold it."""
        mode = self.mode()
        if mode == "off":
            return draft
        for round_ in range(1, MAX_ROUNDS + 1):
            if mode == "external":
                result = verify(draft, self.ctx)
                passed, reason = result["passed"], "; ".join(result["reasons"]) or None
            else:
                self.n += 1
                result = self_critique(self.ctx.client, draft, self.evidence(), trace=self.trace, step=self.n)
                passed, reason = result["decision"] == "accept", result["reason"]
            new, changed = draft, False
            if not passed and round_ < MAX_ROUNDS:
                new = self.rewrite(draft, reason)
                changed = new.strip() != draft.strip()
            self.trace.event("critique", round=round_, mode=mode, result="pass" if passed else "fail",
                             reason=None if passed else reason, changed=changed, draft=draft)
            if passed:
                return draft
            draft = new
        return None

    def rewrite(self, draft: str, reason: str) -> str:
        evidence = self.evidence()
        self.n += 1
        user = (f"The issue:\n{evidence['issue'] or '(not read)'}\n\n"
                f"What the run found, step by step:\n{json.dumps(evidence['steps'], indent=1)}\n\n"
                f"The draft:\n{draft}\n\nThe reviewer's reason to rewrite it: {reason}")
        response = self.ctx.client.create(system=self.ctx.prompt("rewrite.md"),
                                          messages=[{"role": "user", "content": user}])
        self.trace.model_call(self.n, response, purpose="critique")
        label, file = parse_triage_comment(response.text)
        if label and file:
            return format_triage_comment(label, file)
        return response.text.strip() or draft

    def evidence(self) -> dict:
        """What the second pass reads: the issue, and every step's result so far."""
        issue = next((r["content"] for r in self.results.values() if r["tool"] == "read_issue"
                      and r["status"] == "done"), None)
        steps = [{"step": r["id"], "tool": r["tool"], "input": r["args"], "status": r["status"],
                  "paths": r["paths"], "result": r["content"][:EVIDENCE_CHARS]}
                 for r in self.results.values() if r["tool"] != "read_issue"]
        return {"issue": issue, "steps": steps}


def run(ctx) -> None:
    Executor(ctx).run()
