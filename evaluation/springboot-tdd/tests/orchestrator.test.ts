import assert from "node:assert/strict"
import { resolve } from "node:path"
import test from "node:test"

import { loadConfig } from "../src/config.ts"
import { migrateRunManifest } from "../src/orchestrator.ts"

const configPath = resolve(import.meta.dirname, "..", "config", "benchmark_config.yaml")

test("migrates a reevaluated run manifest without losing its original fingerprint", () => {
  const config = loadConfig(configPath)
  const migrated = migrateRunManifest({
    schemaVersion: 1,
    fingerprint: "old-fingerprint",
    app: { commit: config.app.commit, version: config.app.version }
  }, config, "new-fingerprint", "2026-08-12T12:00:00.000Z")

  assert.equal(migrated.schemaVersion, 2)
  assert.equal(migrated.fingerprint, "new-fingerprint")
  assert.equal(migrated.originalFingerprint, "old-fingerprint")
  assert.deepEqual(migrated.app, {
    commit: config.app.commit,
    packageVersion: "1.4.9",
    traceVersion: "39.8.10"
  })
  assert.equal(migrated.reevaluatedAt, "2026-08-12T12:00:00.000Z")
})

test("rejects a malformed run manifest during reevaluation", () => {
  const config = loadConfig(configPath)
  assert.throws(() => migrateRunManifest([], config, "fingerprint"), /run manifest 不是对象/)
})
