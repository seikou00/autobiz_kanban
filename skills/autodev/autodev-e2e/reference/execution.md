# E2E execution commands

Use Python UTF-8 and the current artifact workspace, feature and code workspace.
All CLI values come from current case/Runtime, not a guessed repository or URL.

```bash
python -X utf8 "${pluginPath}/hooks/e2e_quality_check.py" scan --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}" --code-workspace "<current>" --spec-path "<relative spec>" --input "<reviewed source>"
python -X utf8 "${pluginPath}/hooks/e2e_quality_check.py" resolve --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}" --finding-id "<ID>" --status dismissed --reviewer autodev-e2e --rationale "<actual reason>" --input "<reviewed source>"
python -X utf8 "${pluginPath}/hooks/e2e_result_writer.py" sync-quality-gate --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}"
python -X utf8 "${pluginPath}/hooks/run_e2e_command.py" run --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}" --code-workspace "<current>" --case-id "<ID>" --task-id "<TASK>" --spec-ref "<spec#REQ>" --spec-ref "<spec#SCN>" --spec-path "<relative spec>" --entry-url "<URL>" --auth-status "<bypassed|pre_authenticated|not_required>" -- npx --yes --package @playwright/test playwright test "<relative spec>" --grep "<caseId>"
python -X utf8 "${pluginPath}/hooks/e2e_result_writer.py" derive-scenario-coverage --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}"
python -X utf8 "${pluginPath}/hooks/e2e_result_writer.py" finalize --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}"
python -X utf8 "${pluginPath}/hooks/e2e_result_writer.py" validate --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}" --gate
```

Use input-dir for an unresolved import's conservative directory; unresolved input
without a conservative binding blocks PASS. Confirmed findings must be fixed and
rescanned. A dismissed finding requires actual rationale and reviewed sources.
Generated Playwright reports and pending runId are authoritative for resume.

## Optional immutable environment proof

Default repair execution is full. Only a project with a committed, unchanged
`.autodev-e2e-reuse.json` may enable inheritance:

```json
{"version":1,"readOnly":true,"environmentProbe":["node","scripts/describe-test-snapshot.js"]}
```

The project supplies this existing read-only infrastructure probe; never generate a
constant receipt to avoid executing tests. It must describe an actual immutable test
snapshot, returning kind=immutable_snapshot, snapshotId, deploymentDigest, dataDigest,
authDigest, expiresAt (UTC), entryUrl and authStatus. Mutable environments without
snapshot identity are unsupported and use full execution. Probe failure/expiry,
production/shared-fixture/config/case changes or unknown impact also use full scope.
No probe is run in projects without this opt-in policy; no live environment was
provisioned by this change.

The runner checks the provider before/after the actual execution. Repair begin-round
and refresh-reuse derive affectedCaseIds; after local repairs and quality scan run:

```bash
python -X utf8 "${pluginPath}/hooks/e2e_result_writer.py" refresh-reuse --workspace "${pluginWorkspace}/${projectDir}" --feature "${feature}"
```

Only the original runId/roundIndex/Evidence/log/report remain authoritative. Finalize
and the downstream artifact gate independently recheck current environment and
content, plus the original trust chain; new rounds never rewrite or duplicate PASS.
