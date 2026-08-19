/**
 * aikube custom provider for promptfoo — 端到端性能/正确性门禁。
 *
 * 每个用例：把任务文本提交给 AI 集群（aikube run --mode auto），
 * 轮询至该任务全部 Pod 终态，返回：
 *   - SUCCEEDED: 全部 Pod Succeeded（含耗时/路由摘要）
 *   - FAILED:    存在 Failed 或超时
 * latencyMs 由 promptfoo 记录（提交→终态 的端到端延迟）。
 */
import { spawn } from 'node:child_process'

const CLUSTER_ROOT = new URL('../', import.meta.url).pathname

function runCli(args, timeoutMs = 90_000) {
  return new Promise((resolve, reject) => {
    const child = spawn('python3', ['-m', 'aikube', ...args], {
      env: { ...process.env, PYTHONPATH: CLUSTER_ROOT },
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    let stdout = ''
    let stderr = ''
    const timer = setTimeout(() => {
      child.kill('SIGKILL')
      reject(new Error(`aikube ${args.join(' ')} timed out\n${stderr}`))
    }, timeoutMs)
    child.stdout.on('data', (c) => { stdout += c })
    child.stderr.on('data', (c) => { stderr += c })
    child.on('error', (e) => { clearTimeout(timer); reject(e) })
    child.on('close', (code) => {
      clearTimeout(timer)
      if (code === 0) resolve(stdout)
      else reject(new Error(`aikube ${args.join(' ')} exited ${code}\n${stderr}\n${stdout}`))
    })
  })
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function waitTaskSettled(taskName, timeoutMs = 90_000) {
  const t0 = Date.now()
  while (Date.now() - t0 < timeoutMs) {
    const out = await runCli(['get', 'pods'])
    const lines = out.split('\n').slice(2).filter(Boolean)
    const mine = lines.filter((l) => l.includes(taskName))
    if (mine.length > 0 && mine.every((l) => /Succeeded|Failed/.test(l))) {
      return mine
    }
    await sleep(1000)
  }
  throw new Error(`task ${taskName} 未在 ${timeoutMs}ms 内终态`)
}

export default class AikubeProvider {
  id() {
    return 'aikube-cluster'
  }

  async callApi(prompt) {
    const started = Date.now()
    const text = String(prompt).trim()
    const out = await runCli(['run', text, '--mode', 'auto'])
    const taskName = out.split('任务 ')[1].split(' ')[0]
    const pods = await waitTaskSettled(taskName)
    const latencyMs = Date.now() - started
    const failed = pods.filter((l) => l.includes('Failed'))
    const summary = pods.map((l) => l.split(/\s+/).slice(0, 5).join(' ')).join(' | ')
    if (failed.length > 0) {
      return { output: `FAILED: ${summary}`, latencyMs }
    }
    return { output: `SUCCEEDED: ${summary}`, latencyMs }
  }
}
