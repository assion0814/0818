# AgentBench / ToolEmu 接入方案（进阶：通用能力回归 + 安全对抗）

对应文章《基于大模型的Agent进行测试评估的3种方案》的第 1、2 种方案。
本文档给出把「装了 dsh-aikube 插件 / router 预设的 DSH agent」接入这两个
开源项目的具体路径，以及本机（2026-08-19）的环境限制与替代做法。

## 1. AgentBench（通用能力回归）

开源地址: https://github.com/THUDM/AgentBench （arXiv 2308.03688, ICLR 2024）

### 测什么
OS / DB / KG / DCG / LTP / HH / WS / WB 8 个环境的 agent 通用能力。
对我们：验证**装插件/预设后 agent 通用能力不退步**（回归测试），
不测插件自身的调度质量（那是轨迹评估/P50-P95 门禁的职责）。

### 接入路径（DSH 适配器）
AgentBench 的 agent 接口（`AgentBench/agent/`）：

```python
class Agent:
    def __init__(self, name: str, config: str): ...
    def __call__(self, task_information: str) -> str:   # 返回动作字符串
```

DSH 适配器（`agent/dsh_agent.py`，未实现，待环境恢复）：

```python
import subprocess
class DSHAgent(Agent):
    def __init__(self, name, config):
        self.profile = config  # e.g. --profile web / headless + preset
    def __call__(self, task):
        # 把环境步骤作为一条用户消息发给 DSH headless 会话
        r = subprocess.run(["dsh", "--profile", "headless", task],
                           capture_output=True, text=True, timeout=60)
        return r.stdout  # 解析出 agent 的动作（bash 命令 / SQL 等）
```

- OS 环境: DSH 的 bash 工具即动作面（web profile 已含 `tool-bash`）——
  验证 router-standard / aikube 预设下 OS 任务完成率不退化。
- DB 环境: 需把 `sqlite3` 类工具暴露给 agent；DSH 无内置 SQL 工具，
  可用 `bash` 调 `sqlite3`（AgentBench DB 环境自带数据库文件）。

### 本机限制（实测）
- Docker registry 不可用：daemon 配置的 USTC/163 mirror 失效，直连
  docker.io 不通，且无 sudo 无法改 `/etc/docker/daemon.json`
  → AgentBench 的 OS/DB 环境镜像无法构建 → **本机暂无法跑通**。
- 恢复条件：可用 registry（如 docker.m.daocloud.io）配进 daemon 并重启，
  或换一台可拉 Docker Hub 镜像的机器。
- 项目状态：AgentBench 2024 年后基本停更，环境依赖旧，接入成本较高。

## 2. ToolEmu（安全对抗）

开源地址: https://github.com/ryoungj/ToolEmu （arXiv 2309.15817）

### 测什么
LM 仿真工具执行 + 对抗场景生成 + 自动安全评估器。
对我们：验证 `aikube` 工具的**对抗输入下的安全边界**——
越权工具请求、危险参数、角色门控绕过等。

### 接入路径
1. 把 `aikube` 的 10 动作注册为 ToolEmu 工具集（JSON Schema）：

```json
{
  "tools": [{
    "name": "aikube",
    "description": "AI k8s cluster scheduling: init/start/stop/run/get/describe/logs/status/smoke",
    "parameters": {
      "type": "object",
      "properties": {
        "action": {"type": "string", "enum": ["init","start","stop","run",
                   "get","describe","logs","status","smoke"]},
        "text": {"type": "string"},
        "tools": {"type": "string"},
        "pod": {"type": "string"}
      }
    }
  }]
}
```

2. 仿真器（emulator）指向本机 ollama：ToolEmu 基于 OpenAI SDK，
   `config.llm.base_url = http://127.0.0.1:11434/v1` 即可用本地模型仿真。
3. 对抗生成器扫危险场景（如 `run` 携带恶意文本、请求越权 `tools=`、
   `stop` 误用、资源耗尽类输入），安全评估器按风险等级打分。

### 本机限制
- ToolEmu 依赖 conda + torch 等重型环境，本机未安装 conda；
  LM 仿真默认用 GPT-4 类模型（可换 ollama，但小模型仿真质量有限）。
- **替代做法（推荐，已部分实现）**：不仿真，直接对真实集群做对抗——
  我们引擎已有 角色门控（worker 拒绝管理命令）+ ToolSandbox 越权拒绝 +
  控制面 API 面（/exec 类 404），`tests/test_tools.py` 的
  `TestControlPlaneSurface` / `TestRoleGate` 即对抗用例；
  可扩充为独立的 `scripts/adversarial.py` 场景集（危险输入矩阵）。

## 结论与建议

| 方案 | 价值 | 本机状态 | 建议 |
|---|---|---|---|
| 轨迹评估（主） | 调度质量逐步评分 | ✅ 已落地（docs/eval/trajectory-eval.*） | 已入 CI 候选 |
| promptfoo（性能） | 端到端延迟/正确性 | ✅ 已落地（docs/eval/perf-report.md） | 已入 CI 候选，repeat 提到 10+ |
| AgentBench | 通用能力回归 | ⚠️ Docker 受限 | 环境恢复后接 DSHAgent 适配器 |
| ToolEmu | 安全对抗 | ⚠️ conda/模型受限 | 用真实集群对抗脚本替代（建议新增 adversarial.py） |
