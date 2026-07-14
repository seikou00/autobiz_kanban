# Artifact JSON Glob Scanning Design

## Problem

The Code node declares the optional output `cache/code-exploration/**/*.json`, but `board_core.artifacts` only accepts the `specs/**/*.md` glob. `inspect_state --mode run` therefore fails before it can render workflow state whenever it scans the Code node outputs.

This is a contract mismatch, not a Windows path issue. The invalid combination was introduced when the JSON output was added without extending the artifact scanner.

## Considered Approaches

1. Remove `code_exploration_cache` from the Code outputs. This restores `inspect_state`, but hides a real optional artifact and loses board visibility.
2. Accept any glob and infer the suffix from the path. This is flexible but weakens the scanner's path boundary and permits accidental or unsafe recursive scans.
3. Keep an explicit allowlist of artifact ID, exact path, and file suffix. This preserves the existing safety model and adds only the intended JSON cache glob.

The implementation uses approach 3.

## Contract

The scanner supports exactly these recursive artifact globs:

| Artifact ID | Exact path | Matched suffix |
| --- | --- | --- |
| `specs` | `specs/**/*.md` | `.md` |
| `code_exploration_cache` | `cache/code-exploration/**/*.json` | `.json` |

Any other artifact ID, path, suffix, or recursive pattern raises `ValueError`. File artifacts continue through the existing non-glob path.

For a supported glob, the scanner returns sorted workspace-relative file paths. If no file matches, it returns the existing fallback glob path and marks the artifact missing. The JSON cache remains optional according to `board_config.json`.

## Compatibility

The output shape remains unchanged: glob artifacts use `paths`, `artifactStatus`, and `artifactStatusLabel`. Existing specs behavior and error handling remain intact. No cache location, cache schema, or Code task protocol changes.

## Verification

- A focused scanner test creates nested JSON cache files and proves that only `.json` files are returned.
- Existing rejection tests continue to reject unapproved globs.
- The previously failing `inspect_state` integration test must pass.
- The rebuilt plugin archive must contain the fixed scanner and pass `unzip -t` without runtime caches.
