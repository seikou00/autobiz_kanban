# Test engineer assignment protocol

## Assignment

One assignment contains tasks from one Batch with the same `executionLane` and `workspaceRef`. Build assignments in root-plan order. Execute all backend assignments in that stable order, then all frontend assignments in their stable order, followed by any unsupported lane. Do not run assignments concurrently.

The prompt carries:

- `<SCOPE>`, `<SYSTEM>`, and `<UNIT>` references.
- `batch_id`, `task_ids`, `execution_lane`, `workspace_ref`, and resolved repository path.
- `post_implementation=true` and `tdd_rebuild=false`.
- permitted write paths: tests, fixtures, mocks, test helpers, test-environment config, dependency manifests, and the matching lock file.
- selected framework/domain reference output and environment inspection JSON.

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

Use one classification: `test_bug`, `source_bug`, `contract_gap`, `environment`, `flaky`, or `unknown`. Fix test-owned failures in the assignment. For a production defect, return `source_fix_request` with task/spec references, minimal source boundary, failing command, and evidence IDs.

## Return object

Return exactly: `status`, `assignment`, `constraint_files`, `lane`, `framework`, `runner`, `environment_initialization`, `test_targets`, `command_results`, `evidence_ids`, `failure_classification`, `source_fix_request`, `e2e_handoff`, `warnings`.
