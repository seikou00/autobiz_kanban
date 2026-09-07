export const meta = {
  name: "plan-generation",
  description: "Recoverable parallel proposal generation for Draft Plan materialization",
  whenToUse: "在 design_done 后由 plan_workflow_launcher.py 返回的固定脚本启动",
  phases: [
    { title: "准备", detail: "锁定 Design、规格、代码和 writer 输入快照；恢复同一 run" },
    { title: "候选分组", detail: "按 capability 分片并行提出任务分组，再由单一 coordinator 收口" },
    { title: "任务详情", detail: "按已冻结 Task ID 并行生成完整详情，随后一次性提交 Draft" },
    { title: "发布", detail: "配置工程命令、预检并原子生成 plan.json 与 PLAN.md" }
  ]
};

const DEFAULT_MAX_PARALLEL = 3;
const MAX_MAX_PARALLEL = 4;
const DEFAULT_LEASE_TTL_SECONDS = 900;

function normalizeStructuredOutput(value) {
  const text = String(value || "").trim()
    .replace(/\<?think\b[^>]*>[\s\S]*?<\/?think>\s*/gi, "");
  const fence = text.match(/^```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n?```[ \t]*$/i);
  return fence ? fence[1].trim() : text;
}

function unwrap(value) {
  if (value && typeof value === "object" && typeof value.value === "string") return unwrap(value.value);
  if (typeof value !== "string") return value || {};
  try {
    return JSON.parse(normalizeStructuredOutput(value));
  } catch (_) {
    return { ok: false, raw: value, unparsedStructuredOutput: true };
  }
}

function usableString(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function describeArgsShape(value) {
  if (value === undefined) return "undefined";
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  if (typeof value !== "object") return typeof value;
  const keys = Object.keys(value).slice(0, 8);
  return keys.length ? `object(keys=${keys.join(",")})` : "object(empty)";
}

function requireOk(value, label) {
  const result = unwrap(value);
  if (!result || result.ok !== true) {
    throw new Error(`${label} failed: ${JSON.stringify(result)}`);
  }
  return result;
}

function quote(value) {
  return JSON.stringify(String(value));
}

function joinPath(base, ...parts) {
  return [String(base || "").replace(/[\\/]+$/, ""), ...parts]
    .map((part, index) => index === 0 ? part : String(part).replace(/^[\\/]+/, ""))
    .join("/")
    .replace(/\\/g, "/");
}

async function runAgent(prompt, label) {
  return requireOk(await agent(prompt, { label, phase: "Plan" }), label);
}

async function parallelLimited(values, maxParallel, worker) {
  const results = [];
  for (let offset = 0; offset < values.length; offset += maxParallel) {
    const chunk = values.slice(offset, offset + maxParallel);
    results.push(...await parallel(chunk.map((value, index) => () => worker(value, offset + index))));
  }
  return results;
}

const input = unwrap(args);
const feature = input.feature;
const pluginPath = input.pluginPath;
const artifactWorkspace = input.artifactWorkspace || input.workspace;
const codeWorkspaces = input.codeWorkspaces;
const requestedPartitions = Array.isArray(input.partitions)
  ? input.partitions.filter(value => usableString(value))
  : [];
const maxParallel = Number.isInteger(input.maxParallel) && input.maxParallel > 0
  ? Math.min(input.maxParallel, MAX_MAX_PARALLEL)
  : DEFAULT_MAX_PARALLEL;
const leaseTtlSeconds = Number.isInteger(input.leaseTtlSeconds) && input.leaseTtlSeconds > 0
  ? input.leaseTtlSeconds
  : DEFAULT_LEASE_TTL_SECONDS;

if (!usableString(feature) || !usableString(pluginPath) || !usableString(artifactWorkspace)) {
  throw new Error(
    "missing_feature_plugin_path_or_artifact_workspace:" +
    `${describeArgsShape(args)}; start this script as the top-level Workflow tool ` +
    "with args=launcher.workflowArgs (a native object), not a JSON string or a wrapper child call"
  );
}
if (!codeWorkspaces || typeof codeWorkspaces !== "object" || Array.isArray(codeWorkspaces) || Object.keys(codeWorkspaces).length === 0) {
  throw new Error("plan_workflow_code_workspaces_required");
}
if (Object.entries(codeWorkspaces).some(([reference, path]) => !usableString(reference) || !usableString(path))) {
  throw new Error("plan_workflow_code_workspace_invalid");
}

const launcherPath = joinPath(pluginPath, "hooks/plan_generation_launcher.py");
const writerPath = joinPath(pluginPath, "hooks/plan_writer.py");
const featureDir = joinPath(artifactWorkspace, ".autobizdevops", "features", feature);
const groupFile = joinPath(featureDir, ".tmp", "plan_writer", "task-groups.json");
const workspaceArgs = Object.values(codeWorkspaces)
  .map(path => `--code-workspace ${quote(path)}`)
  .join(" ");
const partitionArgs = requestedPartitions.map(value => `--partition ${quote(value)}`).join(" ");

phase("准备");
const ensured = await runAgent(
  `执行 python ${quote(launcherPath)} ensure --workspace ${quote(artifactWorkspace)} --feature ${quote(feature)} ` +
  `${workspaceArgs} ${partitionArgs} --owner-id ${quote(`plan-workflow:${feature}`)} --lease-ttl-seconds ${leaseTtlSeconds}。` +
  `该命令会创建或恢复唯一可恢复 run。只返回该命令的 JSON；不得写入 plan.json、PLAN.md 或 Draft。`,
  "plan-generation-ensure"
);

const runId = ensured.runId;
const leaseToken = ensured.leaseToken;
const snapshotDigest = ensured.snapshotDigest;
if (!usableString(runId) || !usableString(leaseToken) || !usableString(snapshotDigest)) {
  throw new Error("plan_generation_ensure_result_invalid");
}

async function status() {
  return runAgent(
    `执行 python ${quote(launcherPath)} status --workspace ${quote(artifactWorkspace)} --feature ${quote(feature)} --run-id ${quote(runId)}。只返回 JSON。`,
    "plan-generation-status"
  );
}

async function heartbeat() {
  return runAgent(
    `执行 python ${quote(launcherPath)} heartbeat --workspace ${quote(artifactWorkspace)} --feature ${quote(feature)} --run-id ${quote(runId)} --lease-token ${quote(leaseToken)}。只返回 JSON。`,
    "plan-generation-heartbeat"
  );
}

async function release() {
  return runAgent(
    `执行 python ${quote(launcherPath)} release --workspace ${quote(artifactWorkspace)} --feature ${quote(feature)} --run-id ${quote(runId)} --lease-token ${quote(leaseToken)}。只返回 JSON。`,
    "plan-generation-release"
  );
}

async function recoverable(reason) {
  let latest = null;
  try { latest = await status(); } catch (_) { /* preserve the primary reason */ }
  try { await release(); } catch (_) { /* expired leases are safely reclaimable */ }
  return {
    ok: false,
    feature,
    runId,
    finalStatus: "needs_repair",
    reason,
    recovery: latest || { runId, snapshotDigest },
  };
}

async function runGroupWorker(partitionKey, index) {
  const outputPath = joinPath(featureDir, ".tmp", "plan_generation", runId, "worker-input", `group-${index}.json`);
  try {
    return await runAgent(
      `你是 Plan 分组 worker，负责分片 ${quote(partitionKey)}。Feature=${quote(feature)}，输入摘要=${quote(snapshotDigest)}。` +
      `完整读取 ${quote(joinPath(pluginPath, "skills/autodev/autodev-plan/templates/task-groups.json"))}、${quote(joinPath(featureDir, "design.md"))}、${quote(joinPath(featureDir, ".design-contract.lock.json"))}、此分片下的 specs 和相关代码仓库。Design 锁是 API/DATA/D ID 的唯一机器事实源；不得重新校验或改写 design.md。` +
      `只提出候选任务分组，不分配或修改正式 Task ID，不调用 ${quote(writerPath)}，不写 task-groups.json、Draft、plan.json 或 PLAN.md。` +
      `先创建 ${quote(joinPath(featureDir, ".tmp", "plan_generation", runId, "worker-input"))}，再将一个 JSON object 写入 ${quote(outputPath)}；其顶层必须为 {"schemaVersion":1,"snapshotDigest":${quote(snapshotDigest)},"partitionKey":${quote(partitionKey)},"groups":[...],"assumptions":[...]}; groups 仅供 coordinator 归并。` +
      `再执行 python ${quote(launcherPath)} record-group-proposal --workspace ${quote(artifactWorkspace)} --feature ${quote(feature)} --run-id ${quote(runId)} --partition-key ${quote(partitionKey)} --body-file ${quote(outputPath)}。只返回最后命令的 JSON。`,
      `plan-group-${index}`
    );
  } catch (error) {
    await runAgent(
      `记录分组 worker 失败：执行 python ${quote(launcherPath)} fail-group-proposal --workspace ${quote(artifactWorkspace)} --feature ${quote(feature)} --run-id ${quote(runId)} --partition-key ${quote(partitionKey)} --reason ${quote(String(error))}。只返回 JSON。`,
      `plan-group-failed-${index}`
    );
    return { ok: false, partitionKey, error: String(error) };
  }
}

let current = await status();
if (["generating_groups", "needs_repair"].includes(current.status) && current.pendingPartitions.length > 0) {
  phase("候选分组");
  const results = await parallelLimited(current.pendingPartitions, maxParallel, runGroupWorker);
  if (results.some(result => result.ok !== true)) return recoverable("group_worker_failed");
  await heartbeat();
  current = await status();
}

if (["generating_groups", "groups_validated", "needs_repair"].includes(current.status) && current.pendingPartitions.length === 0 && current.taskIds.length === 0) {
  phase("候选分组");
  try {
    await runAgent(
      `你是唯一的 Plan coordinator。读取 ${quote(featureDir + "/.tmp/plan_generation/" + runId + "/group-proposals")} 中的所有 proposal、完整 task-groups 模板、design.md、.design-contract.lock.json、全部 specs 与必要代码证据；Design 锁是 API/DATA/D ID 的唯一机器事实源，不重新校验 design.md。` +
      `统一消解跨分片依赖、Scenario 覆盖、workspace 归属、backend/frontend 顺序和共享写集唯一 owner，按 DAG 拓扑序分配稳定 T001...。` +
      `只写最终候选分组表 ${quote(groupFile)}；它必须符合模板，且不得直接调用 plan_writer。` +
      `完成后执行 python ${quote(launcherPath)} accept-groups --workspace ${quote(artifactWorkspace)} --feature ${quote(feature)} --run-id ${quote(runId)} --lease-token ${quote(leaseToken)} --group-file ${quote(groupFile)}。` +
      `该 launcher 会预检并创建 Draft。只返回最后命令 JSON。`,
      "plan-group-reducer"
    );
  } catch (error) {
    return recoverable(`group_reducer_or_preflight_failed:${String(error)}`);
  }
  await heartbeat();
  current = await status();
}

async function runDetailWorker(taskId, index) {
  const outputPath = joinPath(featureDir, ".tmp", "plan_generation", runId, "worker-input", `detail-${taskId}.json`);
  const previousValidation = current.lastError && current.lastError.validation
    ? `上一次批量校验反馈如下；仅在与 ${quote(taskId)} 相关时据此修正，不要改变 group-owned 字段：${quote(JSON.stringify(current.lastError.validation))}。`
    : "";
  try {
    return await runAgent(
      `你是 Task 详情 worker，只负责 ${quote(taskId)}。Feature=${quote(feature)}，输入摘要=${quote(snapshotDigest)}。` +
      `从 ${quote(groupFile)} 找到该冻结 group，完整读取 task-detail-input.json 模板、design.md、.design-contract.lock.json、关联 spec、当前代码证据；仅消费 Design 锁的 ID，不重新校验 design.md。` +
      previousValidation +
      `生成完整 detail（goal、scope、implementationPoints、acceptanceCriteria、validationCommands、designRefs、decisionIds 等），不得写任何 Draft 或正式计划，绝不调用 ${quote(writerPath)}。` +
      `先创建 ${quote(joinPath(featureDir, ".tmp", "plan_generation", runId, "worker-input"))}，再将 envelope 写入 ${quote(outputPath)}：{"schemaVersion":1,"snapshotDigest":${quote(snapshotDigest)},"taskId":${quote(taskId)},"detail":{...}}。` +
      `再执行 python ${quote(launcherPath)} record-detail-proposal --workspace ${quote(artifactWorkspace)} --feature ${quote(feature)} --run-id ${quote(runId)} --task-id ${quote(taskId)} --body-file ${quote(outputPath)}。只返回最后命令 JSON。`,
      `plan-detail-${index}`
    );
  } catch (error) {
    await runAgent(
      `记录 Task 详情 worker 失败：执行 python ${quote(launcherPath)} fail-detail-proposal --workspace ${quote(artifactWorkspace)} --feature ${quote(feature)} --run-id ${quote(runId)} --task-id ${quote(taskId)} --reason ${quote(String(error))}。只返回 JSON。`,
      `plan-detail-failed-${index}`
    );
    return { ok: false, taskId, error: String(error) };
  }
}

if (["generating_details", "needs_repair"].includes(current.status) && current.pendingTaskIds.length > 0) {
  phase("任务详情");
  const results = await parallelLimited(current.pendingTaskIds, maxParallel, runDetailWorker);
  if (results.some(result => result.ok !== true)) return recoverable("detail_worker_failed");
  await heartbeat();
  current = await status();
}

if (["generating_details", "needs_repair"].includes(current.status) && current.taskIds.length > 0 && current.pendingTaskIds.length === 0) {
  phase("任务详情");
  try {
    await runAgent(
      `执行 python ${quote(launcherPath)} commit-details --workspace ${quote(artifactWorkspace)} --feature ${quote(feature)} --run-id ${quote(runId)} --lease-token ${quote(leaseToken)}。` +
      `该命令会在同一个 Draft 快照中验证全部 detail，并只在全部通过时一次提交。只返回 JSON。`,
      "plan-commit-details"
    );
  } catch (error) {
    return recoverable(`detail_commit_failed:${String(error)}`);
  }
  current = await status();
}

if (["details_committed", "needs_repair"].includes(current.status)) {
  phase("发布");
  try {
    await runAgent(
      `你是 Draft 工程命令 coordinator。读取 Draft 摘要与每个 lane/workspace，选择真实且不执行测试的编译命令。` +
      `仅使用 ${quote(writerPath)} 的 add-compile-command / add-quality-gate-command / add-project-validation-command 配置必要工程命令；不要修改任务详情、分组、设计或正式计划。` +
      `配置后执行 python ${quote(launcherPath)} preflight --workspace ${quote(artifactWorkspace)} --feature ${quote(feature)} --run-id ${quote(runId)} --lease-token ${quote(leaseToken)}。只返回最后命令 JSON。`,
      "plan-engineering-preflight"
    );
  } catch (error) {
    return recoverable(`draft_preflight_failed:${String(error)}`);
  }
  current = await status();
}

if (current.status === "preflight_passed") {
  phase("发布");
  try {
    await runAgent(
      `执行 python ${quote(launcherPath)} finalize --workspace ${quote(artifactWorkspace)} --feature ${quote(feature)} --run-id ${quote(runId)} --lease-token ${quote(leaseToken)}。` +
      `只返回 JSON；finalize 是唯一允许生成 plan.json 与 PLAN.md 的操作。`,
      "plan-finalize"
    );
  } catch (error) {
    return recoverable(`finalize_failed:${String(error)}`);
  }
  current = await status();
}

if (current.status !== "finalized") return recoverable(`workflow_incomplete:${current.status}`);
await release();
return { ok: true, feature, runId, finalStatus: "finalized", taskIds: current.taskIds };
