---
name: autodev-dynamic-quality-gate
description: Minimal quality gate skill target for dynamic workflow overlays.
---

# autodev-dynamic-quality-gate

This skill exists so workflow overlays can reference an installed dynamic quality gate target.

Runtime context is provided by `PLUGIN_WORKSPACE`, `PROJECT_CODE`, `FEATURE_ID`,
`PROJECT_PLUGIN_DIR`, and `FEATURE_DIR`.
