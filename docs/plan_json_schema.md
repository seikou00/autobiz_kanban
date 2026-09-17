# Plan v2 data contract

The planner submits one `autodev.plan.v2` document through `plan_writer.py publish-plan`. It is the only model-authored plan artifact.

```json
{
  "schemaVersion": "autodev.plan.v2",
  "featureId": "feature-id",
  "tasks": [{
    "id": "T001",
    "outcome": "observable delivery result",
    "workspace": "repository-id",
    "dependsOn": [],
    "implementationPoints": ["main behavior or boundary to implement"],
    "testPoints": ["behavior, boundary, or failure path to prove"],
    "refs": {
      "requirements": ["specs/cap/spec.md#REQ-001"],
      "scenarios": ["specs/cap/spec.md#SCN-001"],
      "api": [], "design": [], "data": [], "decisions": []
    },
    "verification": {"intent": "behavior to prove later"}
  }]
}
```

`D-NNN` belongs in `refs.decisions`, which names its primary behavior-delivery task. `refs.design` supplies API/Data context and does not accept decisions. For a UI task, `ui` contains only `pages`, `interactions` and `route`; the writer derives `visualSourceRefs` from every UI capability matched by the task's scenario references.

The writer generates the runtime Bundle: root `plan.json`, Batch plans, `PLAN.md`, Task acceptance records, test intentions, workspace bindings and the parallel pipeline projection. Batch identifiers, command identifiers, execution state and the rendered Markdown are writer-owned.

Keep normal, boundary, permission and failure cases of one behavior together; independently failing assertions do not justify separate tasks. Split broad subsystem goals into small reviewable behavior slices, with no automatic scenario or API count threshold. See `skills/autodev/autodev-plan/references/task-planning.md` for examples.

Plan v2 carries outcome-level `implementationPoints` and `testPoints`, but deliberately excludes file paths, symbols, concrete test paths and shell commands. The points belong to one delivery result: they are a signal to split separate business capabilities that can be delivered independently, never a packing list for unrelated work. Code records actual source changes in its isolated worktree. UTest/E2E turn the test points and `verification.intent` into test assets and executable commands after the implementation surface exists.

The scheduler uses task dependencies and repository bindings to start isolated worktrees. It may run same-repository tasks optimistically; Merge Train resolves actual conflicts. When an unscoped concurrent change may affect another task, evidence invalidation is conservative.

Required task input is `id`, `outcome`, `workspace`, nonempty `implementationPoints` and `testPoints`, `refs.requirements`, `refs.scenarios`, and `verification.intent`. `dependsOn` defaults to `[]`; optional reference lists default to `[]`. Unknown fields are errors rather than silently discarded instructions. Task IDs are unique `TNNN` values and may have gaps; task array order has no scheduling meaning. Writer topologically orders dependencies without changing IDs, rejecting missing dependencies and cycles.

`workspace` is a logical ID: `default` for a single repository or the Git root directory name for explicit repository bindings. Supply each actual repository path with a repeated `--code-workspace`. It is not a predicted module or file list. `refs.api`/`refs.data` use bare `API-NNN`/`DATA-NNN`; `refs.decisions` uses bare `D-NNN`. Optional `refs.design` accepts API/Data IDs or `design.md#ID` for extra context; do not duplicate the same context in both fields. Code resolves design snippets from all these fields. Confirmed public API paths and domain fields are valid context in points, while implementation paths, symbols, algorithms and commands remain Code/UTest choices.

Optional specialized task fields are `mode` (`code` by default, `verified_existing` for already delivered behavior, or `external_dependency` with `external.system`, `external.owner`, `external.trackingRefs`), `stage` (default `parallel`; runtime supports `proto`, `global`, `integration`), and `atomic` (`id: AGNNN`, shared `rationale`). Normal plans omit them. An atomic group is only for a genuinely inseparable delivery of two or three tasks in the same repository and execution lane; never use it to merge unrelated work. Writer still owns Batch IDs and execution records.

Existing `IMPLEMENTATION_SCOPE.json` partitions apply identically to writer and stage gate for scenarios, design IDs and source requirements. With no partition, all known items are in scope. With a partition, only included items are mandatory, and deferred/unpartitioned IDs are reported in `scopeReport` and `PLAN.md`; invalid or overlapping partition IDs fail validation. Do not silently narrow confirmed scope to pass a gate. Published plans cannot be edited in place: before execution, use Plan rollback then publish the corrected complete v2; after execution, use controlled Code recovery.
