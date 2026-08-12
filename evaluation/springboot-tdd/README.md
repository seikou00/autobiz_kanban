# CMBDevClaw SpringBoot TDD evaluation

This package evaluates the plugin through CMBDevClaw, the application platform that installs and runs the packaged plugin. It compares one fixed task under two conditions:

- `control`: CMBDevClaw runs the task without the target plugin.
- `full-chain`: CMBDevClaw installs the exact plugin ZIP and advances the Harness Board chain `dev.specs -> dev.plan -> dev.code -> dev.review -> dev.utest -> dev.verify`.

Both conditions use the same CMBDevClaw build, model configuration, PetClinic commit, task prompt, verifier image, hidden tests, and three-repeat matrix. CMBDevClaw native traces provide Skill, plugin, Harness, timing, tool-call, and token attribution. Docker verification runs only after the agent has finished.

The batch fingerprint includes the resolved model endpoint and model name (never the API key), both fixed run conditions, all three repeats, task/provenance assets, plugin snapshot, workflow, and verifier assets. `compare` refuses incomplete, duplicate, or unbalanced repeat matrices.

## Prerequisites

- CMBDevClaw at commit `0ab3bb562befbd41181b2f4c6436e321fb08bd60`, version `1.4.9`, with its production build and dependencies present.
- Java 17 or newer, Git, and a running Docker daemon. The fixed PetClinic target level and verifier remain Java 17.
- Node.js 22.6 through 24.x.
- A custom CMBDevClaw-compatible model endpoint configured through the environment variables below.

The configuration file keeps a `.yaml` extension for benchmark convention but deliberately uses JSON-compatible YAML syntax. The evaluator has no runtime YAML dependency and rejects ambiguous YAML.

## Setup and validation

```bash
cd evaluation/springboot-tdd
npm install
npm run check
npm run eval -- validate
npm run eval -- list
npm run eval -- preflight
```

If CMBDevClaw is not a sibling of this repository, set `CMBDEVCLAW_PROJECT` to its absolute path.

## Model configuration

```bash
export CMBDEVCLAW_EVAL_BASE_URL='https://example.invalid/v1'
export CMBDEVCLAW_EVAL_MODEL='model-name'
export CMBDEVCLAW_EVAL_API_KEY='secret'
```

The batch command makes paid model calls. Run it only after reviewing `dry-run` and `preflight`:

```bash
npm run eval -- app-smoke
npm run eval -- dry-run
npm run eval -- snapshot
npm run eval -- run
```

`app-smoke` starts an isolated CMBDevClaw instance, proves the target plugin is initially absent, installs the packaged ZIP through the application API, checks Harness Board compatibility, creates the fixed Feature, and validates its first Skill action. The current plugin no longer exposes its legacy `custom` template for new Features, so the driver uses the public `standard` template and the public Harness `skipNode` operation to exclude Biz, E2E, and Ops nodes; the resulting active chain is exactly the six Dev nodes above. It configures the model record but does not invoke the agent or make a model request.

Resume only completed/recorded batch state with:

```bash
npm run eval -- run --resume
```

Select a subset with `--condition control`, `--condition full-chain`, or `--repeat 1,2`. Do not compare a subset as the final benchmark result.

## Verification and reports

```bash
npm run eval -- contract
npm run eval -- evaluate
npm run eval -- compare
```

- `contract` proves that the untouched baseline fails and `gold.patch` passes.
- `evaluate` reruns the fixed hidden verifier without making model calls.
- `compare` writes `reports/comparison.json` and `reports/comparison.md` and rejects mixed fingerprints.

Each run directory retains the task checkout, agent diff, app metadata, native trace copy, Harness stage records, deterministic user-input decisions, verifier output, and final `result.json`. A run is resolved only when build, regression, feature, and integration evidence all pass.

## Fixed task contract

The public task lives at `tasks/springboot-tdd/task.md`. Upstream provenance and the reason for replacing the broken upstream `Visit*` verifier are recorded in `tasks/springboot-tdd/provenance.json`. The 12 replacement hidden tests check the promised weight API, malformed/missing-value side effects, owner/pet isolation on both endpoints, H2 persistence, and newest-first history ordering.
