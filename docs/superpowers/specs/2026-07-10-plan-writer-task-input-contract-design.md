# Plan Writer Single Task Input Template Design

## Goal

Converge Plan's static JSON templates to one direct `add-task` input example without changing the two-layer persisted plan layout.

## Static Template Boundary

`templates/task-input.json` is the only static JSON template. It is a complete non-UI task body suitable for `plan_writer.py add-task --body-file`. It omits writer-owned runtime fields: `status`, `evidenceIds`, `completionEvidenceIds`, `latestPassEvidenceId`, and `completionPolicy`.

There is no static root or batch output example. `plan.json` is initialized by the writer and `plans/Bxxx/plan.json` is projected by the writer from the task collection. The root retains project-level validation commands, batch index, and active batch. Batch plans retain at most five complete tasks, allowing Code to load only the active batch.

## Contract

`plan_writer.py add-task-contract` names the sole input template, required task fields, supported validation kinds, coverage requirements, automatic batch policy, conditional `uiRefs` fields, and the project-validation-command field structure. It explicitly marks root and batch plan files as writer-owned generated artifacts.

For a UI task, callers add `uiRefs` only when `uiRequired` is true and provide `pageRefs`, `interactionRefs`, `visualSourceRefs`, and `frontendRoute`; `scope.pages` must equal `uiRefs.pageRefs`. Callers do not copy a separate UI task example.

## Matrix Exception Compatibility

`task-input.json` keeps an empty `mergedScenarioRefs` array so normal task input remains directly usable. For six through twelve individually expanded scenario refs, `add-task-contract.matrixExceptionExample` provides the full field overlay for `specRefs`, `mergedScenarioRefs`, acceptance criteria, validation command, and rationale. The matrix exception is valid only when `mergedScenarioRefs` exactly equals the task scenario-ref set, one required non-compile behavior command covers every acceptance criterion, and `splitRationale` explains the shared validation loop. Tasks above twelve scenarios remain rejected; the existing API, page, and interaction hard limits are unchanged.

This is a breaking plan-artifact contract. Existing plans with more than five scenario refs must be regenerated through Plan before their next stage-gate validation; no compatibility mode is provided. Range and concatenated scenario references are rejected because they cannot be audited against the structured scenario set.

## Verification

- The sole template omits all writer-owned runtime fields.
- `add-task-contract` exposes the single template, UI condition, project-validation shape, and generated artifact ownership.
- A six-to-twelve scenario matrix task requires complete `mergedScenarioRefs` and one complete required behavior validation; a task with more than twelve scenarios is rejected.
- A UI task without `uiRefs`, or without its required fields, is rejected by task validation.
- A Chinese task created from the sole template through `--body-file` is normalized by the writer and placed in `plans/B001/plan.json`, while root `plan.json` remains task-free.
- Plan skill invariant tests reject references to removed root and batch templates.
