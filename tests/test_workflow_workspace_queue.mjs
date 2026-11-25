import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../workflows/code-batched-execution.workflow.js', import.meta.url), 'utf8');
function harness(agent) {
  const state = { inFlight: new Map(), pending: new Map(), results: new Map(), notifications: [], failureSequence: 0 };
  const waits = [];
  const c = vm.createContext({ workspaceSyncControl: state, workflowAgent: agent,
    usableString: x => typeof x === 'string' && x.length > 0, joinPath: (...x) => x.join('/'),
    pluginPath: '/plugin', artifactWorkspace: '/artifact', feature: 'f', runId: 'run', workflowInvocationId: 'owner',
    WORKSPACE_SYNC_SCHEMA: {}, unwrap: x => x, errorText: x => x instanceof Error ? x.message : JSON.stringify(x),
    absolutePath: x => typeof x === 'string' && x.startsWith('/'), hasFailureSignal: x => x.success === false,
    setTimeout: (fn, ms) => { waits.push(ms); queueMicrotask(fn); },
  });
  vm.runInContext(source.slice(source.indexOf('async function synchronizeUserWorkspace('), source.indexOf('async function settleReadyBatch(')), c);
  vm.runInContext(source.slice(source.indexOf("function inactiveRun("), source.indexOf("function requireSchedulerResult(")), c);
  vm.runInContext(source.slice(source.indexOf('function encodeFailure('), source.indexOf('async function dispatchCommand(')), c);
  return { c, state, waits };
}
const receipt = (ref, status, extra = {}) => ({ success: true, repositoryRef: ref, status, ...extra });

test('a merge during publication causes a later invocation rather than sharing stale success', async () => {
  let resolveFirst, calls = 0;
  const { c, state } = harness(async () => {
    calls++;
    if (calls === 1) return new Promise(resolve => { resolveFirst = resolve; });
    return receipt('repo', 'synced', { lastSyncedSha: 'B' });
  });
  c.enqueueWorkspaceSync(receipt('repo', 'queued', { targetSha: 'A' }));
  await Promise.resolve();
  c.enqueueWorkspaceSync(receipt('repo', 'queued', { targetSha: 'B' }));
  resolveFirst(receipt('repo', 'synced', { lastSyncedSha: 'A' }));
  await c.drainWorkspaceSync();
  assert.equal(calls, 2);
  assert.equal(state.results.get('repo').lastSyncedSha, 'B');
  assert.equal(state.inFlight.size, 0);
});

test('independent repositories publish while another repository is waiting', async () => {
  let release;
  const { c, state } = harness(async (_, options) => options.label.includes('-slow-')
    ? new Promise(resolve => { release = resolve; }) : receipt('fast', 'synced'));
  c.enqueueWorkspaceSync(receipt('slow', 'queued'));
  await c.synchronizeUserWorkspace(receipt('fast', 'queued'));
  assert.equal(state.results.get('fast').status, 'synced');
  assert.equal(state.inFlight.has('slow'), true);
  release(receipt('slow', 'synced'));
  await c.drainWorkspaceSync();
});

test('durable publication retries pass the old limit and preserve warnings until success', async () => {
  let calls = 0;
  const { c, state, waits } = harness(async () => ++calls < 8
    ? receipt('repo', 'retry_wait', { success: false, error: 'disk error', retryAfterSeconds: [5, 30, 120][Math.min(calls - 1, 2)] })
    : receipt('repo', 'synced'));
  await c.synchronizeUserWorkspace(receipt('repo', 'queued'));
  assert.equal(calls, 8);
  assert.equal(waits.reduce((total, ms) => total + ms, 0), 5000 + 30000 + 5 * 120000);
  assert.equal(state.results.get('repo').status, 'synced');
  assert.ok(state.notifications.some(item => item.status === 'retry_wait' && item.error === 'disk error'));
});

test('publication survives repeated model and failure-persistence outages without losing its receipt', async () => {
  let processCalls = 0;
  const { c, state, waits } = harness(async (_, options) => {
    if (options.label.startsWith('workspace-sync-warning-')) throw new Error('failure persistence unavailable');
    if (++processCalls <= 9) throw new Error('model unavailable');
    return receipt('repo', 'synced', { lastSyncedSha: 'same-delivery' });
  });
  await c.synchronizeUserWorkspace(receipt('repo', 'queued', { targetSha: 'same-delivery' }));
  assert.equal(processCalls, 10);
  assert.equal(state.results.get('repo').lastSyncedSha, 'same-delivery');
  assert.ok(waits.every(ms => ms > 0 && ms <= 60000));
});

test('agent dispatch errors are persisted with shell-safe diagnostics and retried', async () => {
  let calls = 0, failures = 0;
  const { c, state } = harness(async (prompt, options) => {
    if (options.label.startsWith('workspace-sync-warning-')) {
      failures++;
      const encoded = prompt.match(/--error-base64 "([A-Za-z0-9+/=]+)"/)[1];
      assert.match(Buffer.from(encoded, 'base64').toString(), /dispatch/);
      return receipt('repo', 'retry_wait', { success: false, retryAfterSeconds: 5, error: 'dispatch failed' });
    }
    if (++calls === 1) throw new Error('dispatch failed: $() "中文"');
    return receipt('repo', 'synced');
  });
  await c.synchronizeUserWorkspace(receipt('repo', 'queued'));
  assert.equal(failures, 1);
  assert.equal(calls, 2);
  assert.equal(state.results.get('repo').status, 'synced');
});

test('a durable queue proceeds past exhausted jobs and records cumulative coverage', async () => {
  let calls = 0;
  const { c, state } = harness(async () => ++calls === 1
    ? receipt('repo', 'queued', { queue: [{ jobId: 'A', status: 'failed', error: 'old failure' }, { jobId: 'B', status: 'queued' }] })
    : receipt('repo', 'synced', { queue: [{ jobId: 'A', status: 'covered', coveredBy: 'B' }, { jobId: 'B', status: 'synced' }] }));
  await c.synchronizeUserWorkspace(receipt('repo', 'queued'));
  assert.equal(calls, 2);
  assert.equal(state.results.get('repo').queue[0].status, 'covered');
  assert.ok(state.notifications.some(item => item.jobId === 'A' && item.status === 'failed'));
});

test('settlement returns while its publication remains active', { timeout: 1000 }, async () => {
  let release;
  const { c, state } = harness(async (_, options) => {
    if (options.label.startsWith('settle-delivery-')) return {
      success: true, status: 'merged', batchId: 'B001', runId: 'run', dispatchGeneration: 1,
      dispatchFinalized: true, deliverySha: 'delivery', mergeCommitSha: 'merge', operationId: 'op', repositoryRef: 'repo', wave: 1,
      workspaceSync: receipt('repo', 'queued'), schedulerSnapshot: { runId: 'run', controlRevision: 1, controlProtocolVersion: 2,
        manifest: { batches: { B001: { status: 'merged', mergeCommitSha: 'merge', commitSha: 'delivery' } } } }
    };
    return new Promise(resolve => { release = resolve; });
  });
  Object.assign(c, { activeDispatchGrants: new Map([['B001', { generation: 1, ownerToken: 'owner' }]]),
    DELIVERY_SETTLEMENT_SCHEMA: {}, requireSuccess: x => x, requireSchedulerResult: x => x, applySchedulerState() {},
    schedulerControlState: {}, deliveryControl: { finalized: new Map(), inFlight: new Map(), repositoryTails: new Map() },
    batchWorkspaces: { B001: { workspaceRef: 'repo' } }, timeoutPerBatch: 14400, runDeferredCleanup: async () => ({}),
    cleanupResults: [], mergeResults: [], markBatchResolved() {}, markCandidateResolved() {} });
  vm.runInContext(source.slice(source.indexOf('async function promoteWithDeltaReview('), source.indexOf('async function validateAndPromoteBatch(')), c);
  vm.runInContext(source.slice(source.indexOf('async function settleReadyBatch('), source.indexOf('async function runDeferredCleanup(')), c);
  const result = await c.settleReadyBatch('B001');
  assert.equal(result.status, 'merged');
  assert.equal(state.inFlight.has('repo'), true);
  release(receipt('repo', 'synced'));
  await c.drainWorkspaceSync();
});


test('a late synced receipt cannot suppress a newer queued merge', async () => {
  let calls = 0;
  const { c, state } = harness(async () => {
    calls++;
    return receipt('repo', 'synced', { lastSyncedSha: 'B' });
  });
  c.enqueueWorkspaceSync(receipt('repo', 'queued', { targetSha: 'B' }));
  c.enqueueWorkspaceSync(receipt('repo', 'synced', { lastSyncedSha: 'A' }));
  await c.drainWorkspaceSync();
  assert.equal(calls, 1);
  assert.equal(state.results.get('repo').lastSyncedSha, 'B');
});

test('cancellation ends a pending publication with no failure retry', async () => {
  const { c, state } = harness(async () => receipt('repo', 'cancelled', { hasPending: true }));
  await c.synchronizeUserWorkspace(receipt('repo', 'queued'));
  assert.equal(state.results.get('repo').status, 'cancelled');
  assert.equal(state.notifications.length, 0);
});

test('publication stops after 20 attempts in one workflow launch and returns failure reason', async () => {
  let processCalls = 0;
  const { c, state, waits } = harness(async (_, options) => {
    if (options.label.startsWith('workspace-sync-warning-')) {
      return receipt('repo', processCalls >= 20 ? 'failed' : 'retry_wait', {
        success: false, error: `sync write error ${processCalls}`, retryAfterSeconds: 5,
      });
    }
    processCalls += 1;
    throw new Error(`sync write error ${processCalls}`);
  });
  const result = await c.synchronizeUserWorkspace(receipt('repo', 'queued'));
  assert.equal(processCalls, 20);
  assert.equal(result.status, 'failed');
  assert.equal(result.success, false);
  assert.match(result.error, /sync write error 20/);
  assert.equal(waits.length, 19);
  assert.equal(state.results.get('repo').status, 'failed');
});

test('busy responses do not consume failure budget and re-enqueue shares exhausted quota', async () => {
  // Part 1: 25 busy polls followed by synced succeed without failing at 20.
  {
    let calls = 0;
    const { c, state } = harness(async () => ++calls <= 25
      ? receipt('repo', 'busy', { retryAfterSeconds: 5 })
      : receipt('repo', 'synced', { lastSyncedSha: 'done' }));
    const result = await c.synchronizeUserWorkspace(receipt('repo', 'queued'));
    assert.equal(calls, 26);
    assert.equal(result.status, 'synced');
    assert.equal(state.results.get('repo').lastSyncedSha, 'done');
  }

  // Part 2: When both process and fail stay unavailable, re-enqueuing in the same
  // resolver reuses the exhausted 20-attempt budget instead of running 40 calls.
  {
    let processCalls = 0;
    const { c } = harness(async (_, options) => {
      if (options.label.startsWith('workspace-sync-warning-')) {
        throw new Error('persist unavailable');
      }
      processCalls += 1;
      throw new Error(`process unavailable ${processCalls}`);
    });
    const first = await c.synchronizeUserWorkspace(receipt('repo', 'queued'));
    assert.equal(first.status, 'failed');
    assert.equal(processCalls, 20);

    const second = await c.synchronizeUserWorkspace(receipt('repo', 'queued'));
    assert.equal(second.status, 'failed');
    assert.equal(processCalls, 20);
  }
});

test('a new resolver does not inherit backoff failures from an earlier launch', async () => {
  let calls = 0;
  const { c } = harness(async (_, { label }) => {
    if (label.startsWith('workspace-sync-warning-')) throw new Error('persistence unavailable');
    calls += 1;
    if (calls === 1) return receipt('repo', 'retry_wait', {
      success: false, jobId: 'A', retryAfterSeconds: 5,
      queue: [{ jobId: 'A', status: 'retry_wait', failureResolver: 'previous-owner', invocationFailures: 19 }],
    });
    if (calls === 2) throw new Error('first failure this launch');
    return receipt('repo', 'synced');
  });
  const result = await c.synchronizeUserWorkspace(receipt('repo', 'queued', { jobId: 'A' }));
  assert.equal(calls, 3);
  assert.equal(result.status, 'synced');
});

test('a late failure for an older job does not consume the next job budget', async () => {
  let calls = 0;
  const { c } = harness(async (_, { label }) => {
    if (label.startsWith('workspace-sync-warning-')) {
      if (calls === 20) return receipt('repo', 'queued', { jobId: 'B', reasonCode: 'workspace_sync_stale_job' });
      return receipt('repo', 'retry_wait', { success: false, jobId: 'A', error: 'job A failed', retryAfterSeconds: 5 });
    }
    if (++calls <= 20) throw new Error('job A process unavailable');
    return receipt('repo', 'synced', { lastSyncedSha: 'B' });
  });
  const result = await c.synchronizeUserWorkspace(receipt('repo', 'queued', { jobId: 'A' }));
  assert.equal(calls, 21);
  assert.equal(result.status, 'synced');
});
