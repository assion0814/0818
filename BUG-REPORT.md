# BUG-REPORT：router-bootstrap 漏导入 extractText（首条用户消息崩溃）

## 现象

每条真实用户消息（`user/message` 事件，`source.kind === 'user'`）触发
`ReferenceError: extractText is not defined`：
- 首条消息的实时捕获失效（`firstUserText` 为空）→ 首次组装时 `sessionMode()`
  看到空 transcript → 路由误判 weak 带
- 修复前运行时复现（本机实测）：

```
崩溃: ReferenceError: extractText is not defined
```

## 根因

`router-bootstrap*.mjs` 的 `session/event` 处理器调用 `extractText(data)`
（第 140 行），但 import 列表（第 22-27 行）漏掉了 `extractText`。
`extractText` 定义于 `router-core.mjs` 且已导出，但未被导入。

## 影响面（8 个文件全部命中）

pin `eff787e9` 与 HEAD × router-standard/router-spec × bootstrap/bootstrap-v1：

| 文件 | 状态 |
|---|---|
| `preset/router-standard/router-bootstrap.mjs` | 命中 → 已修复 |
| `preset/router-standard/router-bootstrap-v1.mjs` | 命中 → 已修复 |
| `preset/router-spec/router-bootstrap.mjs` | 命中 → 已修复 |
| `preset/router-spec/router-bootstrap-v1.mjs` | 命中 → 已修复 |

（HEAD 版本同样存在该问题，本文档仓库内为 pin `eff787e9` 的 vendor 修复副本。）

## 修复方法

每文件一行，import 列表 `coreFor, ` 后追加 `extractText, `：

```diff
-  applyPersona, bandFor, bandOf, coreFor, parseMode, personaFor, sessionMode, testinessFor, clamp01,
+  applyPersona, bandFor, bandOf, coreFor, extractText, parseMode, personaFor, sessionMode, testinessFor, clamp01,
```

统一补丁：`fix-extractText-import.patch`（`git apply` 可直接使用）。

## 验证结果（本机实测）

| 检查 | 修复前 | 修复后 |
|---|---|---|
| 运行时复现（user/message 事件） | `ReferenceError: extractText is not defined` | `OK: 处理器执行无异常` |
| 4 变体模块加载（standard/spec × bootstrap/v1） | — | 全部 loads OK |
| 核心测试 `router.test.mjs`（15 例：分类/persona/coreFor/band/extractText） | — | 15/15 pass |

## 已修复的位置

- 本仓库 `preset/`（vendor 修复副本，见下）
- 本机已安装预设 `~/.dsh/.agent-presets/router-standard/`（bootstrap + bootstrap-v1）
- 本机备份副本 `~/.dsh/scratch/web-profile-backup-20260816/agent-presets-router-standard.bak/`
  （全盘扫描确认无其他副本）

## 推送路径说明（0818 的坑）

`assion0814/0818` 原 `preset/` 是 **submodule 指针**（指向
`yjh051108/dsh-router-standard` @ `eff787e9`），0818 仓库本身不含这些文件。

### 决策记录（2026-08-19）：不再尝试推送上游

对 `yjh051108/dsh-router-standard` **无上游写权限**（实测
`ERROR: Permission to yjh051108/dsh-router-standard.git denied`，且无 GitHub
token 无法走 fork+PR）。**已确认：修复只写入环境与 0818 交付仓库，不再尝试推送上游。**

落地方式：
1. **vendor 修复副本**：`preset/` 由 submodule 改为仓库内直接存放文件
   （eff787e9 树 + extractText 修复），0818 单仓库即可携带修复（commit `bf77a64`）。
2. **环境内全部副本已修复**：活动预设、备份副本（全盘扫描验证）。

如未来获得上游写权限，恢复 submodule 形态的步骤：
1. `git apply fix-extractText-import.patch` → commit → push 到
   `yjh051108/dsh-router-standard`，记下新 SHA
2. 回本仓库：`git -C preset checkout <新SHA>` → `git add preset` → commit → push
   （bump 子模块指针）

## 建议 commit message

```
fix(router): import extractText in router-bootstrap (first-message crash)

router-bootstrap*.mjs call extractText(data) in the session/event handler
but the import list omits it. Every real user/message event threw
ReferenceError, so first-user-text capture failed and the first assembly
classified an empty transcript (false weak band). Add the missing import
in all four variants (router-standard/spec × bootstrap/bootstrap-v1).
```

## GitHub Issue 模板

```markdown
### 现象
首条真实用户消息触发 `ReferenceError: extractText is not defined`，
首轮路由分类回退到空 transcript（误判 weak 带）。

### 复现
1. 安装 router-standard 预设并新建会话
2. 发送任意真实用户消息
3. 观察宿主日志：`ReferenceError: extractText is not defined`
   （router-bootstrap.mjs:140 session/event 处理器）

### 根因
`router-bootstrap*.mjs` 调用 `extractText(data)` 但 import 列表漏掉 `extractText`
（定义与导出均在 router-core.mjs）。

### 影响面
router-standard/router-spec × bootstrap/bootstrap-v1 共 4 个文件
（pin eff787e9 与 HEAD 均命中）。

### 修复
import 列表 `coreFor, ` 后追加 `extractText, `（见 PR/补丁）。
```
