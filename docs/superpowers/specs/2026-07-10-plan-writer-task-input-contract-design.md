# Plan Writer Single Task Input Template Design

## Goal

Converge Plan's static JSON templates to one direct `add-task` input example without changing the two-layer persisted plan layout.

## Static Template Boundary

`templates/task-input.json` is the only static JSON template. It is a complete non-UI task body suitable for `plan_writer.py add-task --body-file`. It omits writer-owned runtime fields: `status`, `evidenceIds`, `completionEvidenceIds`, `latestPassEvidenceId`, and `completionPolicy`.

There is no static root or batch output example. `plan.json` is initialized by the writer and `plans/Bxxx/plan.json` is projected by the writer from the task collection. The root retains project-level validation commands, batch index, and active batch. Batch plans retain at most five complete tasks, allowing Code to load only the active batch.

## Contract

`plan_writer.py add-task-contract` names the sole input template, required task fields, supported validation kinds, coverage requirements, automatic batch policy, conditional `uiRefs` fields, and the project-validation-command field structure. It explicitly marks root and batch plan files as writer-owned generated artifacts.

For a UI task, callers add `uiRefs` only when `uiRequired` is true and provide `pageRefs`, `interactionRefs`, `visualSourceRefs`, and `frontendRoute`; `scope.pages` must equal `uiRefs.pageRefs`. Callers do not copy a separate UI task example.

## Verification

- The sole template omits all writer-owned runtime fields.
- `add-task-contract` exposes the single template, UI condition, project-validation shape, and generated artifact ownership.
- A UI task without `uiRefs`, or without its required fields, is rejected by task validation.
- A Chinese task created from the sole template through `--body-file` is normalized by the writer and placed in `plans/B001/plan.json`, while root `plan.json` remains task-free.
- Plan skill invariant tests reject references to removed root and batch templates.
