/**
 * dsh-aikube — DSH plugin wrapper for the aikube AI k8s cluster scheduling network.
 *
 * Registers:
 *   - service  `aikubeCluster` : cluster capability probe (deterministic)
 *   - tool     `aikube`        : boots a real 1-master-2-node AI cluster in a
 *                                disposable home, schedules spec/react tasks
 *                                through the AI scheduler, and reports the
 *                                resulting node/pod tables. Model-free (mock
 *                                runtime) so it is a deterministic exercise.
 *
 * The bundled Python engine (python/aikube, pure stdlib) is spawned via
 * `python3 -m aikube` with PYTHONPATH/AIKUBE_HOME pinned to plugin-owned paths.
 */
import { spawn } from 'node:child_process'
import { mkdtempSync } from 'node:fs'
import { rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

export const name = 'dsh-aikube'
export const inject = ['tools']

const PYTHON_DIR = join(dirname(fileURLToPath(import.meta.url)), 'python')

function runAikube(args, aikubeHome, timeoutMs = 90_000) {
  return new Promise((resolve, reject) => {
    const child = spawn('python3', ['-m', 'aikube', ...args], {
      env: { ...process.env, PYTHONPATH: PYTHON_DIR, AIKUBE_HOME: aikubeHome },
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    let stdout = ''
    let stderr = ''
    const timer = setTimeout(() => {
      child.kill('SIGKILL')
      reject(new Error(`aikube ${args.join(' ')} timed out after ${timeoutMs}ms\n${stderr}`))
    }, timeoutMs)
    child.stdout.on('data', (chunk) => { stdout += chunk })
    child.stderr.on('data', (chunk) => { stderr += chunk })
    child.on('error', (error) => { clearTimeout(timer); reject(error) })
    child.on('close', (code) => {
      clearTimeout(timer)
      if (code === 0) resolve(stdout)
      else reject(new Error(`aikube ${args.join(' ')} exited ${code}\n${stderr}\n${stdout}`))
    })
  })
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function pollFor(probe, attempts, intervalMs, what) {
  for (let i = 0; i < attempts; i += 1) {
    const out = await probe()
    if (out) return out
    await sleep(intervalMs)
  }
  throw new Error(`poll timeout: ${what}`)
}

async function smokeCluster() {
  const home = mkdtempSync(join(tmpdir(), 'aikube-smoke-'))
  try {
    // 1. kubeadm init 类比：1 主 2 从（节点名必须空格分隔传参）
    await runAikube(['cluster', 'init', '--name', 'testkit',
                     '--nodes', 'k8s-node1', 'k8s-node2'], home)
    // 2. 等全部节点 Ready
    await pollFor(async () => {
      const out = await runAikube(['get', 'nodes'], home)
      const rows = out.split('\n').slice(1).filter(Boolean)
      return rows.length >= 3 && rows.every((r) => r.includes('Ready')) ? out : null
    }, 24, 1000, 'nodes ready')
    // 3. AI 调度：spec 任务 + react 任务（--mode auto 分类路由）
    await runAikube(['run', '设计一个微服务架构上线方案', '--mode', 'auto'], home)
    await runAikube(['run', '修复登录页面的 bug', '--mode', 'auto'], home)
    // 4. 等全部 Pod 终态（Succeeded）
    await pollFor(async () => {
      const out = await runAikube(['get', 'pods'], home)
      return /Pending|Scheduled|Running/.test(out) ? null : out
    }, 36, 1000, 'pods settled')
    const pods = await runAikube(['get', 'pods'], home)
    const nodes = await runAikube(['get', 'nodes'], home)
    await runAikube(['cluster', 'stop'], home)
    return { ok: true, nodes, pods }
  } catch (error) {
    try { await runAikube(['cluster', 'stop'], home) } catch { /* ignore */ }
    throw error
  } finally {
    await rm(home, { recursive: true, force: true }).catch(() => {})
  }
}

export function apply(ctx) {
  ctx.provide('aikubeCluster', {
    version: '0.1.0',
    capabilities: ['cluster-init', 'ai-scheduling', 'node-heartbeat', 'self-healing'],
  })

  ctx.tools.register({
    name: 'aikube',
    description: 'AI k8s cluster scheduling network: boot a 1-master-2-node cluster, ' +
      'schedule AI tasks (spec/react auto-routed) and report node/pod status.',
    parameters: {
      type: 'object',
      properties: {
        action: {
          type: 'string',
          enum: ['smoke', 'status'],
          description: 'smoke: full cluster lifecycle exercise; status: plugin capability info.',
        },
      },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        required: ['value'],
        properties: { value: { type: 'string' } },
      },
      render: (_args, value) => [{ type: 'text', text: value.value }],
    },
    async execute(args) {
      const action = args.action ?? 'smoke'
      if (action === 'status') {
        return { value: JSON.stringify({ component: 'dsh-aikube', version: '0.1.0', engine: 'python3 stdlib' }) }
      }
      const report = await smokeCluster()
      return { value: JSON.stringify(report, null, 2) }
    },
  })
}
