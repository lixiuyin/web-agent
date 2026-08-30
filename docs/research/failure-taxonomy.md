# Failure taxonomy and evidence rules

The taxonomy is designed for recurring-pattern analysis, not post-hoc stories.
Each record carries a taxonomy version, evidence level, detector, evidence
references, and (when observable) onset/recovery steps.

| Layer | Directly observable examples | Causal boundary |
|---|---|---|
| planning | planner request failed; malformed structured action | does not prove faulty reasoning content |
| memory/context | required evidence was absent from the planner-visible context | forgetting requires a known earlier observation and later omission/use failure |
| tool selection | selected tool cannot satisfy the stated subgoal | intent is inferred unless the subgoal is recorded |
| tool execution | timeout, rejected parameters, failed browser action | does not imply the tool was the wrong choice |
| grounding/evidence | cited source was never observed; answer assertion failed | does not identify which internal computation caused it |
| policy | action blocked by an explicit policy decision | distinguish expected protection from agent failure |
| environment | CAPTCHA, site outage, selector drift, inaccessible resource | distinguish transient environment state from system behavior |
| execution control | max steps, unresolved loop, premature completion | recovery policy may be the intervention target |
| feedback | delayed, noisy, partial, or missing verifier signal | only claim an effect under a controlled feedback condition |

Evidence levels:

- `observed`: a trace, tool result, runtime event, or assertion directly records
  the event.
- `candidate`: a deterministic rule proposes a likely subsystem based on
  observable evidence.
- `adjudicated`: a human label or controlled comparison supports the stronger
  interpretation.

The typed representation uses `memory_context`, `tool_selection`, and
`answer_grounding` for the table's slash-separated labels. An `adjudicated`
finding is accepted only when it cites either a controlled intervention or
both retained trace evidence and a human-adjudication record. Human opinion
without a stable evidence reference is rejected. Automatic suite analysis
emits only `observed` and `candidate`; adjudicated findings enter through the
separate `merge_adjudicated_findings` boundary and preserve their detector,
taxonomy version, optional onset/recovery steps, and recurrence signature.

Automatic reports may say “candidate memory/context failure”; they must not say
“the model forgot” solely because a task failed.  Recurrence signatures should
be based on stable observable fields (failure layer, subtype, tool, assertion,
and setting), not free-form planner prose.
