# Assignment 2 report · A plan RepoMind cannot quietly abandon

Every number here that has a field in `reports/LAB_02.json` goes there too: the
checker reads that file (format: `checks/FORMATS.md`, section 10.10). Give each
number its run count. Mark any vendor figure you cite with its source and the date
you read it.

## Part A · The plan artifact
Each step stores its arguments in `args_json`, a string holding an encoded JSON object, rather than giving every tool its own `oneOf` branch. I chose this because the schema stays the same whatever tools are offered and takes nothing from the union budget. The cost is that strict mode can't check the arguments, so before each call the executor parses `args_json` and validates it against the input schema of the tool the step names.

Against the budgets of 24 and 16, `agent/plan_schema.json` uses 0 optional fields and 0 union types, since every field is required and nothing is nullable (an empty `expected_result` fails check 3 anyway, and every step has to name a tool). With 8 object properties and 5 levels of nesting it is also well inside OpenAI's limits of 5,000 and 10. OpenAI's `gpt-4.1-mini-2025-04-14` accepted a request offering the plan tool with `"strict": true` on both development issues, and the two plans it sent back both matched the schema.

Sources, both read on 30 September 2026 (the date the assignment brief gives): the budget of 24 optional and 16 union-typed parameters per request is Anthropic's published strict tool use rule. The limits of 5,000 object properties and 10 levels of nesting, and the rule that every field is required with `additionalProperties: false`, are OpenAI's published Structured Outputs rules.

## Part B · The validator and the boundary

| Plan | Valid | Failures (step, check) |
|---|---|---|
| `good_export_fix.json` | yes | none |
| `good_triage.json` | yes | none |
| `s18_specimen.json` | no | (2, check 2), (3, check 3), (5, check 1); check 4 passes |

`MAX_REPLANS = 2`, so at most three planner calls. The strict schema makes a malformed plan rare, so a third failure in a row would point to a planner that won't converge rather than bad luck, and each extra try costs about 1,200 tokens. A re-planned call is told why the last plan failed (the schema errors and the failed checks, as our code words them) and nothing from the issue.

Of the 54 first plans in the measured runs, 0 broke the schema or failed to end with `post_comment` (0 `schema` lines), and none did in development either. One of the 65 revisions failed the four checks and was rejected (a `revision` line with `valid: false`).

A fill call sees the issue (the `read_issue` step's result), the step's action, expected result and fixed arguments, and the results of the steps it depends on, directly or through other steps. That is what the plan says the step uses, and nothing else. Only the `$fill` arguments are taken from the model's call; fixed arguments always come from the plan. If a step's call fails or goes off plan, it is retried once with the reason, and an exact repeat of a call already made is refused without running.

## Part C · Revision at a checkpoint
After the first search, and after any search a revision added or changed, the checkpoint fails if the search found nothing, found no `.ts` or `.tsx` file under `src/`, or filled all 20 result slots. `MAX_REVISIONS = 2`: one repair, and a second if the repaired search also fails.

In code the check is `search_check` in `ways/planned.py`. It fails when the search finds nothing, when none of the hits is in a source file (a `.ts` or `.tsx` file under `src/`), or when the hits fill the whole limit of 20. In that last case the list was cut off in path order, so the term was too common to point anywhere. That third clause was necessary: TypeScript's `export` keyword makes "export" match every source file, so "at least one source file" on its own never failed in development. The reviser sees the plan (whose filled-in arguments are still `"$fill"`), the step results without their `args`, and a reason our code writes from counts and paths. It never sees the issue or a query.

In the 54 measured planned runs, 40 revised at least once and 25 of those used up `MAX_REVISIONS = 2`. Of the 65 revisions, 31 came from the cut-off rule, 26 from no source file and 8 from a search that found nothing. That is close to the brief's "revising at every step" trap, so I checked it against step counts. A run averaged 1.2 revisions against 4.0 steps in the first plan (5.0 after revision) and 5.1 tool calls, revision happened only at a search and never at a read or the comment, and the bound held: no run went past 2.

The cut-off rule fires this often because of `search_repo` itself. It returns at most 20 hits in path order, and the executor's first term was usually an everyday word from the issue ("upload", "export", "currency"). So the check was catching a real weakness in the first search, not noise. Of the 31 cut-off revisions, 26 led to a new search: one was rejected by the validator, and four were followed by no further search. Of those 26, the next search came back under the cap with a source file in it 15 times.

Revision did not buy successes. Runs with no revision passed 10 of 14, with one revision 5 of 15, and with two 4 of 25. That mostly reflects the issues, not the revisions. The checkpoint never fired on 4201, whose first search found the template directly, and the planned runs passed it 9 of 9. It fired on each of the other five issues: 16 times in 9 runs on 4202 (0 passes in any configuration), and 12 or 13 times in 9 runs on each of 4203 to 4206. So a revision marks a run whose first search missed, which is a hard run whatever happens next. A tighter check, for example dropping the cut-off clause, would revise less, but in development that clause was the only one that ever fired on 4192, the dev issue whose obvious search fills the list with docs.

## Part D · A second pass before posting
The four checks are in `verify` in `agent/critique.py`:

1. Parses: a `label:` line with one of bug, feature, question or docs, and a `file:` line with a path or none.
2. Exists: a named path is a file under `fixtures/repo`.
3. Seen: a named path is in the `paths` of one of this run's own `tool_call` lines.
4. Agree: a question names `none`, and every other label names a file.

`MAX_ROUNDS = 2`: check, rewrite once with the reason, then check again. A draft that still fails is held and the run escalates. Every round writes a `critique` line.

Slide 41's three gates:

| Mode | A check can find the error | The critic knows more than the agent | A limit and a log |
|---|---|---|---|
| `off` | no check | no critic | nothing to bound |
| `self` | no: it rereads the same issue and the same results | no: it is the same model with the same evidence | yes: `MAX_ROUNDS`, one `critique` line per round |
| `external` | yes, for format, existence, provenance and label/file agreement; not for a seen file that is the wrong cause | yes: the repository and the run's own trace, which the drafting call never checked | yes: `MAX_ROUNDS`, one `critique` line per round |

## Part E · Detectors and the diff
In `agent/diff.py`, a `tool_call` line that carries a `plan_step` belongs to that step if it calls the step's tool, and every retry of a step counts as that step. A line with no `plan_step` (the transcribed runs) goes to the earliest unmatched plan step whose tool it calls and whose fixed arguments it carries, meaning every argument the plan writes other than `"$fill"` has the same value in the call. Each such step takes one call, so an identical second call is `extra`. A matched step is `executed` if its first call comes after the first calls of every earlier matched step, and `reordered` otherwise.

**The looping run.**

- Q1 fires on turns 1 and 3 (`search("export")`), and on turns 2 and 4 (`read(ReportsPage.tsx)`).
- Q2 fires on turns 1 and 2, answered from the trace. There is no `depends_on` and the results carry no paths, so I judged a result unused when the run later fetched exactly the same thing again: if turn 1's 247 results had been used, turn 3 had no reason to exist. Turns 3 and 4 are plausibly used, since turn 5's `exportButton` likely came from reading the reports page.
- Q3 fires on steps 3, 4, 5 and 6, which never ran (identify, fix, verify, open a PR).
- Q4: only in outline, because its steps were too coarse to check ("search for export" had no expected result and "identify the bug" had no tool), so the agent had no record of its own progress and repeated itself until the turn limit.

**Slide 51's run.**

- Q1 fires on trace entries 2 and 4 (the `tool_call` lines at steps 2 and 4, 09:14:08 and 09:14:31), the same `search_repo("export")`. These are trace entries, not plan steps: plan step 4 is the edit, which the diff labels executed.
- Q2 does not fire: every step but the last is in a later step's `depends_on`.
- Q3 fires on plan step 6, "confirm the button works", which never ran.
- Q4: yes, the plan matched the task and even ended with the right outside test, but the run reported success after its re-run still failed, so this is premature stopping rather than a bad plan.
- The diff: 1 executed, 2 executed, 3 executed, 4 executed (the edit), 5 executed (the second `run_tests`, which carries the plan's fixed `test: test_export_csv`), 6 skipped. The repeated search, trace entry 4, is `extra`.

## Part F · What the plan and the second pass bought
Three sweeps a configuration: a pilot, not a settled difference.

All 72 runs used gpt-4.1-mini-2025-04-14, 18 per configuration and 3 per issue. A run passes by the grounded check: Assignment 1's `check()` in `eval/check_triage.py`, which grades the comment carrying the run's `run_id` in the tracker against the answer key. The run itself never sees it, so it is the outside test of success, not one of Part D's four checks. Development runs on 4191 and 4192 are in `runs/` too. The checker and `eval/lab02.py` count only the evaluation issues.

| Measure | Agent | Planned, off | Planned, self | Planned, external |
|---|---|---|---|---|
| Passes, of 18 | 10 | 8 | 6 | 5 |
| Per issue, 4201 to 4206, of 3 each | 3, 0, 2, 2, 2, 1 | 3, 0, 2, 0, 3, 0 | 3, 0, 2, 0, 1, 0 | 3, 0, 0, 0, 2, 0 |
| Spread: the lowest and highest sweep, of 6 | 3 to 4 | 2 to 3 | 1 to 3 | 1 to 2 |
| Tokens per run | 12,538 | 9,064 | 11,655 | 9,446 |
| Latency per run: median, and slowest | 6.9 s, 9.5 s | 8.1 s, 13.6 s | 9.7 s, 13.8 s | 9.8 s, 14.1 s |
| Plan overhead: the mean share of prompt tokens, and how it changes with run length | | 29.1%; the plan call alone falls from 25% (4 calls) to 7% (10 to 11 calls) | 23.9%; 19% (5 calls) to 6% (11) | 28.0%; 25% (4 calls) to 7% (11) |
| Second-pass overhead: the mean share of prompt tokens | | | 18.4% | 6.0% |
| Runs with at least one exact repeat | 6 | 0 | 0 | 0 |
| `off_plan` lines | | 0 | 0 | 0 |
| Revisions per run: mean, and maximum | | 1.17, 2 | 1.28, 2 | 1.17, 2 |
| Second-pass rounds per run: mean, and maximum | | | 0.89, 2 | 1.06, 2 |
| Drafts changed; of those fixed, and broken | | | 1; 0, 0 | 5; 2, 0 |
| Drafts held at the bound | | | 0 | 1 |

Cells are empty where a measure does not apply.

The table comes from `eval/lab02.py`. `python -m eval.lab02` prints it, and `python -m eval.lab02 --write` also fills `results` and `context` in `reports/LAB_02.json`.

Plan overhead by the number of model calls in the run. Each cell gives the number of runs in the group, the mean share of prompt tokens taken by plan and revise calls, and the share taken by the first plan call alone.

| Model calls | Planned, off | Planned, self | Planned, external |
|---|---|---|---|
| 4 | 2 runs: 25%; 25% | | 3 runs: 25%; 25% |
| 5 | 4 runs: 23%; 18% | 3 runs: 19%; 19% | 3 runs: 16%; 16% |
| 6 | 5 runs: 34%; 14% | | 1 run: 31%; 14% |
| 7 | 1 run: 44%; 12% | 2 runs: 26%; 12% | 2 runs: 43%; 12% |
| 8 | | 5 runs: 28%; 10% | 5 runs: 31%; 10% |
| 9 | 2 runs: 35%; 10% | 4 runs: 25%; 9% | 1 run: 38%; 11% |
| 10 | 2 runs: 25%; 7% | | |
| 11 | 2 runs: 25%; 7% | 4 runs: 21%; 6% | 3 runs: 24%; 7% |

The plan call alone shrinks as runs get longer, from a quarter of the prompt tokens at 4 calls to about 7% at 11. The plan-and-revise share doesn't shrink the same way, because longer runs are mostly longer from revising, and each revision is a planner-sized call. In `self`, rounds per run can average below 1 because a run that escalates before its comment (3 of 18) never reaches the second pass.

My first measurement of the three planned configurations exposed a bug in my executor. A failed search's record was overwritten before the checkpoint saw it, so no failed search was ever revised. I fixed it, discarded those runs with `run.py --discard` (their tokens stay in the ledger), and measured all three configurations again on one version of the code. The agent's 18 runs don't use that code, so I kept them.

1. **What did the plan buy RepoMind, concretely?**

   The plan gave no extra successes: 8 of 18 with the plan against 10 of 18 for the agent, a pilot gap of two runs. What it produced instead:
   - Logged plans: 118 `plan` lines over the 54 planned runs. 54 were first versions, written before any issue text was read, and 64 were revised versions, each logged with the model and the prompt that wrote it. The 65th revision failed the validator, so it was rejected and never logged as a plan: 54 + 64 = 118.
   - A gate before every tool call: the schema and the four checks ran on 54 first plans and 65 revisions. They rejected 0 plans and 1 revision, and the rejected revision never ran.
   - No repeats ran, and no off-plan calls: 0 runs with an exact repeat in the trace, against 6 of 18 for the agent, and 0 `off_plan` lines. The zero comes from a check in the executor (`check_input` in `ways/planned.py`), not from the plan alone: 3 planned runs, one in each configuration, tried an exact repeat of a search. The executor refused it before it ran, the retry repeated it again, and all 3 runs escalated. Those are 3 of the 12 escalations below.
   - One approval point per run: a person could approve 54 plans, one per run, before anything runs. Without a plan, the agent's 18 runs would need an approval for each of their 149 tool calls (8.3 a run). The planned runs made 275 tool calls, all inside a plan that passed the schema check and the validator, either a first version or an accepted revision. This is slide 54's approval point for Lecture 5.
   - Clean handoffs: 12 planned runs escalated with a named reason and posted nothing. The agent ended 6 runs at `turn_limit`: one of them (4206, sweep 1) had already posted and passed, and the other 5 posted nothing. A sixth agent run that posted nothing (4203, sweep 1) stopped with `model_stopped`. None of these gave a reason.
   - A diff for every run: over the final plan versions, 244 steps executed, 24 were skipped (all in the escalated runs) and there were 0 extra calls.
   - A trail for every revision: 65 revisions, each with a reason built from counts. 31 were for a cut-off search, 26 for no source file, and 8 for a search that found nothing.

   The plan also cut tokens per run by 28% (9,064 against 12,538) and made runs slower (median 8.1 s against 6.9 s).
2. **Where did it not pay?** Slide 52's four cases, applied to the six issues, then
   one observation beyond them.

   - One step: none of the six issues is.
   - Steps known in advance: this is RepoMind's case. 53 of 54 first plans were the same four steps (read, search, read, post), which is a workflow in all but name. The plan bought logging and a gate, not a better route.
   - The plan costs more than the work: on no issue did planning cost more than executing. Per run in `planned_off`, plan and revise tokens against execution tokens were 1,261 against 3,324 on 4201 (the cheapest ratio) and 4,441 against 5,849 on 4202 (the closest, at 76%). On 4201, which every configuration solved 3 of 3, the plan is the most overhead for the least gain. On 4202, which passed 0 of 3 everywhere, the planner and reviser spent the most and bought nothing.
   - Nothing reads the plan: this does not apply, because code reads every plan:
     - the schema check and the validator, before anything runs;
     - the executor, which takes each step's tool, fixed arguments and `depends_on` from it;
     - the checkpoint and the reviser, which repair it;
     - the diff, and the course's checker.

     What is missing is a person. No one approves a plan before it runs yet, so the approval point in Q1 exists but goes unused until Lecture 5.
   - Beyond slide 52's four cases, rigidity: the plan cost two issues the agent could solve, 4204 (agent 2 of 3, plan 0 of 3) and 4206 (1 against 0). The executor may not follow a trail beyond its steps, and at most two revisions per run could not get from the issue's words to `src/util/dates.ts` or `src/ingest/dedupe.ts`.
3. **What did each second pass buy, and what did it cost?**

   - `self` caught nothing. It accepted 14 of the 15 drafts it read on their first round and changed 1: on 4206 it called a well-formed draft malformed and rewrote it to the same label and file. It broke no passing draft. It cost 1,979 tokens a run (18.4% of prompt tokens). Against `off` it added 0.9 s of mean latency (9.92 s against 8.99 s) and 1.6 s of median latency (9.7 s against 8.1 s). Its passes (6) fell below `off`'s (8), but those are different runs, and the gap comes from execution (4205: wrong files accepted), not from the critic.
   - `external` caught 5 drafts and fixed 2. On 4205 it twice caught a question that named `docs/ARCHITECTURE.md`, and each rewrite to `file: none` passed the grounded check. Once on 4205 the label line was missing, the rewrite still failed to parse, and the draft was held (1 escalation, nothing posted). It broke no passing draft. It cost 653 tokens a run (6.0%), all of it in rewrites, since the check itself is code with no model call. The time cost is mixed: mean latency was 0.1 s below `off`'s (8.90 s against 8.99 s), but the median was 1.7 s above (9.8 s against 8.1 s). The check adds almost nothing, so the differences come from which runs needed a rewrite or extra revisions, and with 18 runs a mode neither difference is settled.
   - Issue 4206, whose text blames `src/export/csv.ts`, passed 0 of 3 under every second-pass mode:
     - `off` posted csv.ts once, a `question` naming csv.ts once, and one unparseable comment.
     - `self` accepted csv.ts 3 times.
     - `external` failed csv.ts twice as never seen in the run. The rewrites moved to `src/api/reports.ts`, which the run had seen and which was still wrong.

     The seen check stopped an ungrounded file but couldn't choose the right one, because the error was semantic and no check here can find that.
   - On the gates, `self` passes only the limit-and-log gate. `external` passes all three, though only for structural errors.
4. **Which failure did no detector catch?**

   Premature stopping. One clear case, and one borderline case, each of which only the tracker could reveal:
   - Clear: the agent on 4203, sweep 1, read `src/ingest/parser.ts`, the right file, then ended with `model_stopped` and posted nothing. Its trace shows a clean stop.
   - Borderline: `planned_off` on 4206, sweep 1, ended `completed` with every plan step executed, so the expected-result check found nothing skipped. But its comment began "bug: The export CSV issue…" with no `label:` or `file:` line. This is as much a format failure as premature stopping: `off` never checks the draft, and `external`'s parse check would have caught it. The run's own record says it finished, and only the outside test shows the comment can't be read.

   I knew because the grounded check reads the tracker by `run_id`. Nothing inside either run disagreed with its ending. Planned runs that completed with a wrong file (for example 4204's `src/notifications/rollup.ts`, 3 times) are the same failure one level up: finished by every in-run measure, wrong by the outside test.

## Part G · Note-taking against compaction
`COMPACT_AT = 1000` tokens (characters ÷ 4). Past that, every entry but the most recent is replaced by one model-written summary. The progress file, `runs/<run_id>.progress.json`, is written after every tool call and every step. It holds an `about` line ("a record of what this run has already seen, not a list of things to do"), `steps_done`, and `paths_seen` (also as `paths`), which lists every path a search returned or a read read, in order. It never holds a search term.

| Run | Issue | Progress file shown | Compacted before the questions | Answers right, of 6 |
|---|---|---|---|---|
| notes-4203-s01-334d0c | 4203 | no | yes | 4 |
| notes-4203-s02-2a9980 | 4203 | no | yes | 4 |
| notes-4204-s01-f5a0d8 | 4204 | no | yes | 4 |
| notes-4204-s02-87f89a | 4204 | no | yes | 3 |
| notes-4206-s01-abc87a | 4206 | no | yes | 3 |
| notes-4206-s02-76e6fd | 4206 | no | yes | 3 |
| notes-4203-s01-b77907 | 4203 | yes | yes | 6 |
| notes-4203-s02-eb958a | 4203 | yes | yes | 6 |
| notes-4204-s01-5373a7 | 4204 | yes | yes | 6 |
| notes-4204-s02-5aa632 | 4204 | yes | yes | 6 |
| notes-4206-s01-22d086 | 4206 | yes | yes | 6 |
| notes-4206-s02-6a56cd | 4206 | yes | yes | 6 |

Without the file, 21 of 36 answers were right. With it, 36 of 36.

1. **What did the progress file keep that compaction lost,** and what did the file
   cost in tokens?

   - It kept the early paths. All 15 wrong answers without the file were a "no" for a path the run had in fact seen, and there was no false "yes". 13 of the 15 were documentation and changelog files (`CHANGELOG.md`, `README.md`, `docs/API.md`, `docs/ARCHITECTURE.md`) that the first search returned and the summary folded into a sentence about the issue. The file kept every one, because it lists paths and compaction never touches it.
   - It cost about 2,630 tokens a run, 14,610 against 11,977 (+22%), since its text goes into every executor call and the question call. It also costs a file write per step.
2. **Which context operations did RepoMind need?**

   Part F has two "longest" runs:
   - By model calls: the agent on 4202, with 12 calls, 20,076 tokens and a largest single prompt of 4,421 tokens.
   - By tokens: a planned run with `external` on 4205, with 11 calls, 20,124 tokens and a largest prompt of 3,247.

   Either way, no prompt in Part F was larger than 4,421 tokens, far inside the model's context window, so the answers below hold for both.
   - Structured note-taking: not needed at this length. Nothing was compacted in Part F, because compaction (and `COMPACT_AT`) exists only in the notes way, `ways/notes.py`. The planned way that Part F measures has none, so nothing was lost. It would cost about 2,600 tokens a run (Part G's measure), and Part G shows it is what to add first if runs get long enough to compact. The plan with its step statuses is already a small progress record.
   - Sub-agent isolation: not needed. No subtask's working context was worth hiding, and a sub-agent for the search phase would cost its own calls plus whatever its summary left out, which here would be exactly the paths Part G shows a summary drops.
   - Just-in-time retrieval: partly in place already. The run carries search hits (paths) and reads a file only when a step names it. Doing more of it (passing pointers instead of the 4,000-character excerpts the second pass sees) would cost one extra `read_file` per file used.
   - Compaction: not needed. At about 4,400 tokens a prompt there is nothing to compact. Its cost would be a summarizing call, plus 15 of 36 recall questions wrong without a file beside it.

## Assistance

**AI Tool Used:** Claude Code

I used Claude Code during this assignment. It assisted me with implementing and refining parts of `eval/lab02.py`, refining the prompts in `prompts/`, and running the experiments. I examined the execution traces, and analyzed the results to answer the assignment questions.

For the written work, I wrote the initial drafts of the report sections myself. I then used Claude Code to check the report for grammatical issues, inconsistencies, and clarity. I reviewed the feedback, corrected claims that did not match the experimental traces, and made the final decisions about the interpretation and presentation of the results.

I ran `make test` and confirmed that all tests passed. I also ran `make check-02` and confirmed that all 84 checks passed with no integrity flags, both before and after the corrections. I checked that each configuration's measured runs used a consistent code version and that runs from before the executor fix were retained and stored in `runs/discarded/`.

Finally, I ran `python -m eval.lab02` to reproduce the Part F and Part G tables from `runs/` and the tracker. I cross-checked the reported counts and token usage against the execution traces, verified the file and line references in `ARCHITECTURE.md`, and confirmed that `verify` and the run code do not read `fixtures/answers.yaml` or `eval/check_triage.py`. While reviewing my initial draft, Claude Code identified two claims in Part F that needed correction. One was concerning exact repeated searches and another concerning premature stopping. I reviewed these findings and corrected both claims in the final report.