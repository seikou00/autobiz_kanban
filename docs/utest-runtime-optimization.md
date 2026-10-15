# Plan V2 UTest runtime optimization

UTest now uses finite test presets, bound test-environment writes, cached module
diagnostics and generated Bash execution. Plan V2 remains unchanged: outcomes,
implementationPoints, testPoints and verification.intent define the work;
runtime owns dependency/preset/file/command choices.

## Current Batch protocol

Every runtime command takes the current Workflow-provided values:

```text
--workspace <artifact workspace> --feature <feature>
--run-id <parallel run> --batch-id <Batch>
--batch-worktree <native worktree> --task-id <TASK>
--owner-token <current lease token>
```

Use `--environment-target-id` when the inspector selected one of several module
candidates. Do not author repository/cwd values or change Plan to name modules.

1. Run `hooks/utest_runtime.py prepare`. Reuse its environment/preset, explicit
   capability hints, missing requirements and reference. Hints from the POM are
   not proof that Maven has resolved dependencies. Existing JUnit 4 is supported.
2. Read the actual behavior and closest relevant example. If evidence requires
   another finite template, run `select --preset <ID> --reason <evidence>` once
   before execution. Use JUnit with Mockito where necessary; do not perform a
   version matrix search. Unsupported static chains return `testability_gap`.
3. Add/adjust required test dependencies and test plugin configuration in POM.
   Use an explicit write/edit or `apply-pom --content-file <complete candidate>`.
   A candidate file must be in this worktree or system temporary directory.
   The test-stage authorization does not require reopening an implemented TASK.
4. Resolve/probe required capabilities once. Existing `ready` can still lack H2,
   Mockito inline/Byte Buddy or security-test. All Maven commands execute
   directly in Bash, including version queries,
   dependency diagnostics and classpath generation. For Maven setup use
   `utest_runtime.py preflight-setup` with the current Batch binding and exact
   argv. Execute the returned `bashCommand` for `status=prepared`; reuse the
   actual result for `status=cached`. The script streams output, captures Maven
   and tee statuses and supervises descendants. `run_utest_command.py --kind
   setup --record-only` only records logs and cache; direct Maven launch is
   rejected. Setup success is not passing test Evidence and does not satisfy
   the first-test deadline. Successful and failed diagnostics are cached by
   module/build configuration/argv/PATH, using a new Bash cache namespace so
   previous native-launch failures cannot poison the result. Refresh with
   `preflight-setup --refresh-diagnostics` only after the external dependency
   environment changes.
5. For dependency classes, use `lookup-class` with `--classpath-file` and a fully
   qualified `--class-name`. `inspect-class` caches source-jar reads or bounded
   javap. Searches only touch listed module dependencies, preserving duplicate
   class order; never recursively scan the local Maven repository.
6. Write a real assigned behavior test, then `preflight --test-file <relative
   file> -- <exact argv>`. Maven requires `-Dtest`/`-Dit.test` naming the assigned
   files; Gradle requires `--tests`. Execute the returned `bashCommand` exactly.
   Runtime validates the current lease/stage/files/command, preserves the real
   process and tee statuses and monitors a dedicated test process group.
   Registration exit 0 only means registration succeeded; read the actual
   result/exitCode/logExitCode. Retain output after a registration failure and
   retry registration without launching Maven again.
7. Test-owned repairs use the same target/argv with `--retry-kind test_bug` once.
   Source repairs need failing evidence, a new reviewed production commit and
   `--retry-kind source_repair` once. Runtime counters persist across recovery.
8. When blocked or exhausted, retain output/assets, record the deferred issue,
   seal permitted changes and release the lease through the existing Workflow.
   Do not inspect plugin sources for a bypass or invent a passing result.

## POM ownership

Guard, runtime writer and worktree sealer share `utest_test_environment.py`.
They compare the mixed POM's production projection against Git HEAD. Direct
test-scoped dependencies and Surefire/Failsafe plugins may change. Parent/BOM,
shared properties, profiles and production dependencies remain implementation
changes. Versions follow the current dependency-management convention; concrete
versions may be supplied in test dependencies when needed. Do not disable tests,
mask failures or add exclusions to obtain a green invocation.

This permits test-only edits and subsequent UTest sealing without an active
production task. It does not classify every XML/POM as a test asset. Changed file
collection is NUL-delimited so nested Chinese/spaced POM paths work correctly.

## Durable state and validation

Environment and execution state are stored under the current run/Batch's
`utest` directory. They are Runtime-owned; generated Bash scripts have a stored
content digest and cannot be edited or replayed. Each script rechecks its attempt
before launching tests. Evidence registration is idempotent after success and
retains its pending Evidence ID when result registration needs repair.

Initial budgets: first test command within 600 seconds; total stage at most 1800
seconds, command at most 300 seconds. Workflow can narrow these budgets. The
stage includes discovery/setup/repair, and recovery/lease renewal cannot extend
the stored deadline. Static/class inspection is bounded to 30 seconds.

PASS requires actual executed cases. Zero, all-skipped, missing execution proof
or failed output capture is BLOCKED, including Maven exit 0. Real exit codes are
preserved in Evidence rather than replaced with synthetic failure codes. Java
runner output or fresh target-matching JUnit reports provide count proof.
Missing dependency resolution and testCompile errors are distinguished from
production compilation problems. Mapper/H2 tests never replace uncovered
service authorization/login branches.

## Verification scope

Regression tests exercise real temporary Git worktrees, test POM authorization
and sealing, legacy JUnit, environment/cache invalidation, exact targets, Bash
execution, replay prevention, timeout evidence, repair counters, zero/skipped
cases and class/source-jar caching. No business Maven module or Windows machine
is available in this checkout: the real legacy service and Windows Git Bash
process-tree termination still need an integration run in that environment.
