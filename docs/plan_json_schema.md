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

The writer generates the runtime Bundle: root `plan.json`, Batch plans, `PLAN.md`, Task acceptance records, test intentions, workspace bindings and the parallel pipeline projection. Batch identifiers, command identifiers, execution state and the rendered Markdown are writer-owned.

Plan v2 carries outcome-level `implementationPoints` and `testPoints`, but deliberately excludes file paths, symbols, concrete test paths and shell commands. Code records actual source changes in its isolated worktree. UTest/E2E turn the test points and `verification.intent` into test assets and executable commands after the implementation surface exists.

The scheduler uses task dependencies and repository bindings to start isolated worktrees. It may run same-repository tasks optimistically; Merge Train resolves actual conflicts. When an unscoped concurrent change may affect another task, evidence invalidation is conservative.
