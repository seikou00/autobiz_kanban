import { mkdir } from "node:fs/promises"
import { resolve } from "node:path"

import { launchCmbDevClaw } from "./cmbdevclaw-app.ts"
import {
  assertPluginAbsent,
  createHarnessBinding,
  getRunDetail,
  installPlugin,
  type InstalledPlugin
} from "./cmbdevclaw-driver.ts"
import { createPluginSnapshot } from "./snapshot.ts"
import type { BenchmarkConfig, RunPlan } from "./types.ts"
import { assertWorkflowAction, decodeRunDetail } from "./workflow.ts"
import { prepareRunDirectories } from "./workspace.ts"

export async function runAppSmoke(config: BenchmarkConfig): Promise<{
  root: string
  plugin: InstalledPlugin
  harness: {
    projectId: string
    slug: string
    templateId: string
    skippedNodes: string[]
    nodeId: string
    nodeStatus: string
    nextSkill: string
  }
}> {
  const root = resolve(config.reportRoot, `_app-smoke-${Date.now()}`)
  const plan: RunPlan = {
    id: "app-smoke",
    condition: "full-chain",
    repeat: 1,
    taskId: config.task.id,
    reportDir: root
  }
  const dirs = await prepareRunDirectories(plan)
  await mkdir(dirs.repo, { recursive: true })
  const snapshot = await createPluginSnapshot(config, resolve(root, "batch"))
  const session = await launchCmbDevClaw(config, dirs)
  try {
    await assertPluginAbsent(session, config.plugin.expectedName)
    const plugin = await installPlugin(session, config, snapshot)
    const binding = await createHarnessBinding(
      session,
      config,
      plugin,
      dirs.pluginWorkspace,
      dirs.repo,
      "app-smoke"
    )
    const detail = decodeRunDetail(await getRunDetail(session, binding))
    const firstNode = config.workflow.nodes[0]!
    const firstSkill = config.workflow.skills[firstNode]!
    assertWorkflowAction(detail, firstNode, firstSkill)
    return {
      root,
      plugin,
      harness: {
        projectId: binding.projectId,
        slug: binding.slug,
        templateId: binding.templateId,
        skippedNodes: binding.skippedNodes,
        nodeId: detail.currentNodeId,
        nodeStatus: detail.currentNodeStatus,
        nextSkill: firstSkill
      }
    }
  } finally {
    await session.close()
  }
}
