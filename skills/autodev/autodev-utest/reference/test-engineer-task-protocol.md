# UTest assignment protocol

UTEST_ASSIGNMENT is the complete Plan testing context. It binds one Batch/lane/
workspace, TASK testPoints/verificationIntent/AC and runtime-owned nonGoals. Use its
promptContent unchanged; batchPlanPath is traceability, not permission to reopen Plan.
Runtime owns repository/cwd selection. Use inspector-resolved paths and actual
manifests/lockfiles. Constraint conflicts are contract_gap, not grounds to guess.
For resolve_project_from_code, inspect the current Task's implementation/build
files and retry with the matching target; do not ask the user or mark it blocked.

Every Runtime call carries current workspace, feature, run-id, batch-id,
batch-worktree, task-id and owner-token; take exact operations from Runtime.
prepare returns environment, finite preset, engineeringContext and policy. Read brief
and precise allowedReads first. Known valid facts need no repeated lookup; missing
API facts use bounded lookup/resolve/query and immediate publish/fail closure.

Frontend UTest is temporarily skipped with frontend_utest_temporarily_disabled,
without setup, test generation/execution or compile; retain its test intent.
Within an assignment execute sequentially; across Batches the fixed scheduler owns
concurrency and shared environment constraints.

preflight-setup executes selected module diagnostics via generated Bash; cached
successes/misses/failures are reused until inputs/environment change. Missing test
POM dependencies may be applied once via semantic guard; do not change production
settings. For static isolation read the selected legacy-static-test-patterns only.

preflight requires actual test files and precise argv, freezes target/binding and
returns bashCommand. Execute it unchanged; it preserves PIPESTATUS/tee and registers
Evidence. Recovery keeps deadlines and counters. awaiting_record only records the
original captured output. Actual cases are mandatory for PASS.

Classify test_bug/source_bug/contract_gap/environment/flaky/unknown. Test-owned repair
is limited to the original target/argv once. Source bugs require current failed
Evidence and source-bug validator; return source_fix_request. Workflow compiles and
reseals targeted production repair, records single_repair_accepted and resumes the
same test without another Review. Exhaustion follows Runtime deferred/blocked policy,
retaining logs/assets/uncovered goals; no alternative runner or command to bypass it.

Use the Workflow result schema. For direct test-engineer task calls return status,
assignment, constraint_files, lane, framework, runner, environment_initialization,
test_targets, command_results, evidence_ids, failure_classification,
source_bug_attestation, source_fix_request, e2e_handoff and warnings. Reports/coverage
are derived from actual results; no mandatory empty sections or blanket regression.
