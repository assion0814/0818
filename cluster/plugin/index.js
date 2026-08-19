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
    const env = { ...process.env, PYTHONPATH: PYTHON_DIR }
    if (aikubeHome) env.AIKUBE_HOME = aikubeHome
    const child = spawn('python3', ['-m', 'aikube', ...args], {
      env,
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
    // 1. kubeadm init 类比：1 主 2 从（节点名必须空格分隔传参；
    //    --port-base 用独立端口段，避免与用户默认集群(12379/16443)冲突）
    await runAikube(['cluster', 'init', '--name', 'testkit',
                     '--nodes', 'k8s-node1', 'k8s-node2',
                     '--port-base', '24000'], home)
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
    version: '0.1.1',
    capabilities: ['cluster-init', 'ai-scheduling', 'node-heartbeat', 'self-healing',
                   'tool-least-privilege'],
  })

  ctx.tools.register({
    name: 'aikube',
    description: 'AI k8s cluster scheduling network: schedule AI tasks through a ' +
      '1-master-2-node cluster (AI scheduler classifies spec/react/mixed/weak, ' +
      'routes to tool-appropriate nodes, tool least-privilege enforced). ' +
      'Actions: init/start/stop (cluster lifecycle), run (submit task), ' +
      'get (nodes|pods|tasks|tools), describe, logs, status, smoke.',
    parameters: {
      type: 'object',
      properties: {
        action: {
          type: 'string',
          enum: ['init', 'start', 'stop', 'run', 'get', 'describe', 'logs',
                 'status', 'smoke'],
          description: 'init: 初始化并启动集群; start/stop: 集群启停; ' +
            'run: 提交任务(自动分类路由); get: 查询 nodes|pods|tasks|tools; ' +
            'describe/logs: 任务详情与输出; status: 插件能力信息; smoke: 全链路测试.',
        },
        text: {
          type: 'string',
          description: '任务文本（action=run 必填）。',
        },
        kind: {
          type: 'string',
          enum: ['nodes', 'pods', 'tasks', 'tools'],
          description: '查询类型（action=get 必填）。',
        },
        mode: {
          type: 'string',
          enum: ['auto', 'spec', 'react', 'mixed', 'weak'],
          description: '任务模式（action=run，默认 auto 由 AI 调度器分类）。',
        },
        tools: {
          type: 'string',
          description: '任务显式请求的工具（逗号分隔，如 code_exec,file_read）。',
        },
        pod: {
          type: 'string',
          description: 'Pod 名（action=describe|logs 必填）。',
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
      const action = args.action ?? 'status'
      if (action === 'status') {
        return { value: JSON.stringify({ component: 'dsh-aikube', version: '0.1.1',
                                         engine: 'python3 stdlib',
                                         scheduling: 'AI 分类 + 工具最小权限路由' }) }
      }
      if (action === 'smoke') {
        const report = await smokeCluster()
        return { value: JSON.stringify(report, null, 2) }
      }
      // —— 真实调度动作：操作默认集群（~/.aikube，AIKUBE_HOME 环境变量可覆盖）——
      if (action === 'run') {
        if (!args.text) throw new Error('action=run 需要 text 参数')
        const cmd = ['run', args.text, '--mode', args.mode ?? 'auto']
        if (args.tools) cmd.push('--tools', args.tools)
        const out = await runAikube(cmd, defaultHome())
        return { value: `任务已提交到 AI 集群:\n${out}` }
      }
      if (action === 'get') {
        if (!args.kind) throw new Error('action=get 需要 kind 参数 (nodes|pods|tasks|tools)')
        const out = await runAikube(['get', args.kind], defaultHome())
        return { value: out }
      }
      if (action === 'describe' || action === 'logs') {
        if (!args.pod) throw new Error(`action=${action} 需要 pod 参数`)
        const out = await runAikube([action, args.pod], defaultHome())
        return { value: out }
      }
      if (action === 'init' || action === 'start' || action === 'stop') {
        if (action === 'init') {
          // 幂等：已有集群配置则启动，否则初始化
          const home = defaultHome() ?? ''
          const exists = await runAikube(['cluster', 'status'], home)
            .then(() => true)
            .catch(() => false)
          const out = exists
            ? await runAikube(['cluster', 'start'], home)
            : await runAikube(['cluster', 'init', '--name', 'dsh',
                               '--nodes', 'k8s-node1', 'k8s-node2'], home)
          return { value: out }
        }
        const out = await runAikube(['cluster', action], defaultHome())
        return { value: out }
      }
      throw new Error(`未知动作: ${action}`)
    },
  })
}

// 真实调度动作使用用户默认集群状态目录（~/.aikube，AIKUBE_HOME 环境变量可覆盖）
function defaultHome() {
  return process.env.AIKUBE_HOME || undefined
}
