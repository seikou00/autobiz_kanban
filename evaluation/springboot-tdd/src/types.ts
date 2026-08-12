export const FAILURE_CLASSES = [
  "setup",
  "app_launch",
  "plugin_load",
  "agent",
  "user_input",
  "timeout",
  "verifier",
  "infrastructure",
  "task"
] as const

export type FailureClass = (typeof FAILURE_CLASSES)[number]
export type ConditionId = "control" | "full-chain"

export interface TaskConfig {
  id: string
  promptPath: string
  sourcePath: string
  provenancePath: string
  repoUrl: string
  repoCommit: string
}

export interface AppConfig {
  projectPath: string
  commit: string
  version: string
  traceVersion: string
  mainEntry: string
  electronBin: string
}

export interface PluginConfig {
  root: string
  packageScript: string
  expectedName: string
  expectedVersion: string
}

export interface ModelConfig {
  id: string
  displayName: string
  baseUrlEnv: string
  modelEnv: string
  apiKeyEnv: string
  maxTokens: number
  maxOutputTokens: number
  temperature: number
}

export interface WorkflowConfig {
  feature: string
  projectDir: string
  terminalCheckpoint: string
  nodes: string[]
  skills: Record<string, string>
}

export interface VerifierConfig {
  image: string
  platform: string
  hiddenTestPath: string
  goldPatchPath: string
  testClass: string
  imagePullTimeoutMs: number
  timeoutMs: number
}

export interface ConditionConfig {
  id: ConditionId
  pluginEnabled: boolean
}

export interface BenchmarkConfig {
  schemaVersion: number
  benchmarkId: string
  repeats: number
  reportRoot: string
  task: TaskConfig
  app: AppConfig
  plugin: PluginConfig
  model: ModelConfig
  workflow: WorkflowConfig
  verifier: VerifierConfig
  conditions: ConditionConfig[]
  timeouts: {
    stageMs: number
    totalMs: number
  }
}

export interface RunPlan {
  id: string
  condition: ConditionId
  repeat: number
  taskId: string
  reportDir: string
}

export interface ProcessResult {
  argv: string[]
  cwd: string
  exitCode: number | null
  signal: NodeJS.Signals | null
  stdout: string
  stderr: string
  durationMs: number
  timedOut: boolean
}

export interface PluginSnapshotFile {
  path: string
  size: number
  sha256: string
}

export interface PluginSnapshotManifest {
  schemaVersion: 1
  createdAt: string
  zipPath: string
  zipSha256: string
  pluginName: string
  pluginVersion: string
  gitHead: string
  gitBranch: string
  gitDirty: boolean
  dirtyDiffSha256: string
  files: PluginSnapshotFile[]
  fingerprint: string
}

export interface UserInputOption {
  label: string
  description: string
}

export interface UserInputQuestion {
  header: string
  id: string
  question: string
  options: UserInputOption[]
}

export interface UserInputRequest {
  requestId: string
  threadId: string
  questions: UserInputQuestion[]
  createdAt: string
}

export interface UserInputAnswer {
  type: "option"
  questionId: string
  optionIndex: number
  label: string
  description: string
}

export interface UserInputDecision {
  requestId: string
  threadId: string
  questions: UserInputQuestion[]
  answers: Record<string, UserInputAnswer>
  submittedAt: string
}

export interface WorkflowNextAction {
  slashSkill?: string
  userMessage?: string
  dialogTips?: string
  preferredPlugin?: { id?: string; name?: string }
}

export interface WorkflowStageRecord {
  nodeId: string
  skill: string
  threadId: string
  beforeStatus: string
  afterStatus: string
  nextSkill?: string
  startedAt: string
  endedAt: string
  outcome: "success" | "error" | "timeout"
  error?: string
  userInput: UserInputDecision[]
}

export interface AgentTrace {
  traceId: string
  threadId: string
  startedAt: string
  endedAt: string
  durationMs: number
  userMessage: string
  modelId: string
  modelName?: string
  steps: Array<{ toolCalls: Array<{ name: string }> }>
  modelCalls?: Array<{ tokenUsage?: Record<string, number> }>
  nodes?: unknown[]
  totalToolCalls: number
  outcome: "success" | "error" | "cancelled" | "unknown"
  appVersion?: string
  usedSkills: string[]
  skillSource?: string[]
  triggerSource?: string
  harnessProjectId?: string
  harnessFeatureSlug?: string
  harnessNodeName?: string
  harnessNodeStatus?: string
  harnessAdapterId?: string
  harnessAdapterName?: string
  harnessAdapterVersion?: string
  metadata?: Record<string, unknown>
}

export interface TraceSummary {
  traceIds: string[]
  threadIds: string[]
  durationMs: number
  toolCalls: number
  modelCalls: number
  inputTokens: number
  outputTokens: number
  totalTokens: number
  usedSkills: string[]
  skillSource: string[]
}

export interface TestCaseResult {
  name: string
  status: "passed" | "failed" | "error" | "skipped"
  durationSeconds: number
  message?: string
}

export interface VerifierResult {
  build: ProcessResult
  regression: ProcessResult
  hidden: ProcessResult
  tests: TestCaseResult[]
  scores: {
    build: number
    regression: number
    feature: number
    integration: number
  }
  resolved: boolean
}

export interface RunResult {
  schemaVersion: 2
  runId: string
  benchmarkId: string
  taskId: string
  condition: ConditionId
  repeat: number
  fingerprint: string
  completed: boolean
  resolved: boolean
  failureClass?: FailureClass
  error?: string
  scores: {
    build: number
    regression: number
    feature: number
    integration: number
  }
  tests: { passed: number; failed: number; errors: number; skipped: number }
  usage: TraceSummary
  traceIds: string[]
  stageCount: number
  appVersion: string
  appPackageVersion: string
  pluginVersion?: string
}
