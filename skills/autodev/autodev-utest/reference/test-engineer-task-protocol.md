# Test engineer assignment protocol

## Assignment

One assignment contains tasks from one Batch with the same `executionLane` and `workspaceRef`. Use the stable order returned by `utest_assignment_router.py`: backend, frontend, then any unsupported lane. Do not run assignments concurrently.

The task description starts with the router-produced `<UTEST_ASSIGNMENT>` block and carries:

- `<SCOPE>`, `<SYSTEM>`, and `<UNIT>` references.
- `batchPlanPath`, `batchId`, `executionLane`, `workspaceRef`, and the environment inspector's resolved repository/module paths.
- per task: `id`, `implementationPoints`, `nonGoals`, and `validationLocations` containing only plan `repo/cwd`.
- `post_implementation=true` and `tdd_rebuild=false` from the hook contract.
- permitted write paths: tests, fixtures, mocks, helpers and test configuration. A mixed `pom.xml` may change during test for test-scoped dependencies and Surefire/Failsafe configuration; use the semantic guard/runtime writer and sealer. No active production task run is required. Preserve production dependencies, parent/BOM, profiles, shared properties and production sources.
- selected framework/domain reference output and environment inspection JSON.

Use the `<UTEST_ASSIGNMENT>` block verbatim; `batchPlanPath` is traceability evidence, so do not reopen plan.json to fetch or restate fields. Cover `testPoints` and `verificationIntent`, using `implementationPoints` for location. Plan V2 does not supply `nonGoals`, test paths, dependency coordinates or argv. Use `validationLocations` only to confirm the assigned repo/cwd; do not author or pass repository/cwd paths to UTest scripts. Use only the environment inspector's resolved paths for file access. Generate the precise test argv from the real runner after the file exists; the runtime preflight freezes it and the runner resolves current task/AC binding.

## Finite preparation and execution

Run `utest_runtime.py prepare` with the Workflow's run/Batch/worktree/lease and task IDs. Use its preset and capability hints; a declaration is not proof of artifact resolution. Apply missing test dependencies once via `apply-pom`, then resolve/probe the chosen capabilities. `select --preset ... --reason ...` permits one evidence-backed adjustment before execution. Static mocks require the resolved version/maker; reuse verified PowerMock combinations only. Unisolatable static chains return `testability_gap`, retaining service testPoints even when mapper tests pass.

Use `lookup-class --classpath-file ... --class-name fully.qualified.Class` for dependency lookup. No recursive local-repository scan; cached misses/timeouts are not retried without an environment change. All Maven commands run directly in Bash, including version queries and setup diagnostics. Use `utest_runtime.py preflight-setup` with the same live Batch/lease binding and exact argv; execute its returned `bashCommand` only for `status=prepared`, and reuse actual results for `status=cached`. Python only supervises and registers via `--kind setup --record-only`. Read-only Maven diagnostics are cached by module/configuration/argv, including negative results; old native-launch cache entries are invalidated. Setup never counts as behavioral execution or resets the first-test deadline. Read the recorded result/exitCode/logExitCode; recording success is not Maven success.

After writing the test, call `preflight --test-file ... -- <exact argv>` and run the returned `bashCommand`. It binds the immutable target, enforces first-execution/total deadlines, supervises test descendants and records real Bash/tee statuses. Successful process exit with zero or unverified cases is BLOCKED. Repair test assets once with `--retry-kind test_bug`; controlled source repair uses `--retry-kind source_repair` once. Recovery preserves counters/deadlines. If registration fails, use its retained output rather than rerunning. Blocked paths must still seal valid test changes and release the lease through the Workflow.

Resolve `framework` only from actually opened `<SYSTEM>`/`<UNIT>` documents. Resolve runner and package manager only from the assigned repository's manifests, lock files, and test configuration. A conflict is a blocking `contract_gap`. If the constraint does not name a framework, use repository facts and add a warning.

## Routing

| Target | Domain |
|---|---|
| Pure function | `unit` / frontend `fundamentals` |
| Hook or composable | `logic` |
| Store | `state` |
| Component | `component` |
| Router, page, API adapter | `integration` |

Real-browser, multi-page, and real-network paths return `e2e_handoff` only.

## Failure result

Use one classification: `test_bug`, `source_bug`, `contract_gap`, `environment`, `flaky`, or `unknown`. Fix test-owned failures in the assignment. A `source_bug` requires the source-bug validator's attestation for a FAIL target with non-zero current Evidence, matching taskDigest/commandId, and real AC covers. Do not fix production code in UTest: return `source_fix_request` with the attestation, failing command, root cause, and Batch boundary. The fixed Batch Workflow will repair production code, compile/reseal, and restart Review plus this assignment.

## Return object

Return exactly: `status`, `assignment`, `constraint_files`, `lane`, `framework`, `runner`, `environment_initialization`, `test_targets`, `command_results`, `evidence_ids`, `failure_classification`, `source_bug_attestation`, `source_fix_request`, `e2e_handoff`, `warnings`.
