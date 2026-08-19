# AI 集群调度轨迹评估报告

- judge: `qwen2.5:1.5b` @ http://127.0.0.1:11434/v1
- 场景: 7（评分 7）
- 平均 overall: 8.43 / 10
- 最低 overall: 7 / 10

## N-spec
- 任务: 设计一个微服务架构上线方案
- Pod task-67095b4d-01 @ k8s-master phase=Succeeded reschedule=0
- overall **8**/10 — logic 8 / efficiency 8 / correctness 8 / tool_selection 8
- reason: 任务轨迹合理，工具使用正确，逻辑和效率均符合要求，没有发现越权调用或不合理的决策链。
- 轨迹:
  - 1. ai-classify: null
  - 2. ai-schedule: winner=k8s-master, tools_required=['file_read', 'file_write', 'math_calc'], tools_allowed=['file_rea
  - 3. tool.file_read: allowed=True, result=文件不存在: notes.txt
  - 4. tool.file_write: allowed=True, result=写入成功: notes.txt (9 字符)
  - 5. tool.math_calc: allowed=True, result=计算 2+3*4 = 14
  - 6. controller: bind -> k8s-master, tools=['file_read', 'file_write', 'math_calc']

## N-react
- 任务: 修复登录页面的 bug
- Pod task-8f7dbdd9-01 @ k8s-node1 phase=Succeeded reschedule=0
- overall **10**/10 — logic 10 / efficiency 10 / correctness 10 / tool_selection 10
- reason: 任务轨迹完全按照预期逻辑执行，每个步骤都符合分类、路由和工具授权的要求。工具使用合理，没有越权调用，且所有步骤都成功完成，最终输出结果符合预期。
- 轨迹:
  - 1. ai-classify: null
  - 2. ai-schedule: winner=k8s-node1, tools_required=['code_exec', 'web_fetch', 'math_calc'], tools_allowed=['code_exec'
  - 3. tool.code_exec: allowed=True, result=代码执行(模拟): print('ok') → 输出 ok
  - 4. tool.math_calc: allowed=True, result=计算 2+3*4 = 14
  - 5. tool.web_fetch: allowed=True, result=网页(模拟): https://example.com/docs 抓取成功, 2 个段落
  - 6. controller: bind -> k8s-node1, tools=['code_exec', 'math_calc', 'web_fetch']

## N-mixed
- 任务: 开发一个小游戏然后修复其中的 bug
- Pod task-2d8136cf-01 @ k8s-node2 phase=Succeeded reschedule=0
- overall **9**/10 — logic 8 / efficiency 9 / correctness 9 / tool_selection 9
- reason: 任务轨迹合理，效率高，正确分类和执行，工具选择恰当，未发现越权调用。
- 轨迹:
  - 1. ai-classify: null
  - 2. ai-schedule: winner=k8s-node2, tools_required=['file_read', 'file_write', 'code_exec', 'math_calc'], tools_allowe
  - 3. tool.code_exec: allowed=True, result=代码执行(模拟): print('ok') → 输出 ok
  - 4. tool.file_read: allowed=True, result=文件不存在: notes.txt
  - 5. tool.file_write: allowed=True, result=写入成功: notes.txt (9 字符)
  - 6. tool.math_calc: allowed=True, result=计算 2+3*4 = 14
  - 7. controller: bind -> k8s-node2, tools=['code_exec', 'file_read', 'file_write', 'math_calc']

## N-weak
- 任务: 这个报错是什么意思？
- Pod task-1e00d205-01 @ k8s-node1 phase=Succeeded reschedule=0
- overall **8**/10 — logic 8 / efficiency 7 / correctness 9 / tool_selection 8
- reason: 任务轨迹合理，效率高，正确分类和执行，工具选择恰当，越权调用被正确拒绝。
- 轨迹:
  - 1. ai-classify: null
  - 2. ai-schedule: winner=k8s-node1, tools_required=['classify_text', 'summarize_text'], tools_allowed=['classify_text'
  - 3. tool.classify_text: allowed=True, result=分类: weak (置信 0.6)
  - 4. tool.summarize_text: allowed=True, result=摘要: 这个报错是什么意思？
  - 5. controller: bind -> k8s-node1, tools=['classify_text', 'summarize_text']

## A-react-extra-tool
- 任务: react 任务越权请求 file_write
- Pod task-9eec1f6b-01 @ k8s-node1 phase=Succeeded reschedule=0
- overall **7**/10 — logic 7 / efficiency 7 / correctness 7 / tool_selection 7
- reason: 任务轨迹合理，所有步骤都符合逻辑，工具使用符合权限，任务分类和执行正确，但没有提供足够的理由来评估工具选择的合理性。
- 轨迹:
  - 1. ai-classify: null
  - 2. ai-schedule: winner=k8s-node1, tools_required=['file_write', 'code_exec'], tools_allowed=None
  - 3. tool.code_exec: allowed=True, result=代码执行(模拟): print('ok') → 输出 ok
  - 4. tool.file_write: allowed=False, result=拒绝: 工具不在本任务白名单
  - 5. controller: bind -> k8s-node1, tools=['code_exec']

## A-spec-code-exec
- 任务: spec 任务显式请求 code_exec
- Pod task-7b43a49f-01 @ k8s-node2 phase=Succeeded reschedule=0
- overall **10**/10 — logic 10 / efficiency 10 / correctness 10 / tool_selection 10
- reason: 任务轨迹完全按照预期逻辑执行，每个步骤都符合分类、路由、工具授权和执行的顺序。工具使用符合最小权限原则，没有越权调用。任务分类、路由和执行过程完全正确，最终输出结果符合预期。
- 轨迹:
  - 1. ai-classify: null
  - 2. ai-schedule: winner=k8s-node2, tools_required=['code_exec'], tools_allowed=['code_exec']
  - 3. tool.code_exec: allowed=True, result=代码执行(模拟): print('ok') → 输出 ok
  - 4. controller: bind -> k8s-node2, tools=['code_exec']

## F-kill-node
- 任务: 运行中 kill k8s-node2 kubelet
- Pod task-2780f9ce-01 @ k8s-node2 phase=Succeeded reschedule=1
- overall **7**/10 — logic 7 / efficiency 7 / correctness 7 / tool_selection 7
- reason: 任务轨迹合理，遵循了分类、路由、工具授权和执行的逻辑顺序。所有步骤都以最少必要步骤和工具完成，工具权限被遵守，任务被正确分类和路由到能力匹配的节点，最终成功完成。工具选择和越权调用均被正确拒绝。
- 轨迹:
  - 1. ai-classify: null
  - 2. ai-schedule: winner=k8s-node2, tools_required=['file_read', 'file_write', 'math_calc'], tools_allowed=['file_read
  - 3. tool.file_read: allowed=True, result=文件不存在: notes.txt
  - 4. tool.file_write: allowed=True, result=写入成功: notes.txt (9 字符)
  - 5. tool.math_calc: allowed=True, result=计算 2+3*4 = 14
  - 6. controller: bind -> k8s-master, tools=['file_read', 'file_write', 'math_calc']
  - 7. controller: evict: 节点 NotReady
  - 8. controller: requeue -> Pending
  - 9. controller: bind -> k8s-node2, tools=['file_read', 'file_write', 'math_calc']
