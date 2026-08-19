# dsh-routing-suite — Injector × Reasoning-Mode Routing × AI Cluster Suite

One repository for the full stack: the **runtime surgery table** (dsh-super-injector,
restart-free plugin management), the **reasoning-mode routing presets**
(dsh-router-standard: task-aware reasoning-mode routing) and the **aikube AI
cluster scheduling network** (a k8s-cluster architecture recreated as an AI
scheduling fabric: 1 control-plane + N nodes, pure Python stdlib).

[中文](README.md) | English

## Install chain (three steps)

```powershell
# 1. Clone the suite (preset/cluster in-repo; injector as submodule)
git clone --recurse-submodules https://github.com/yjh051108/dsh-routing-suite.git
cd dsh-routing-suite

# 2. One-shot install (injector assembly + preset copy + restart prompt)
.\install.ps1
```

Or manually:

```powershell
# Step 1: assemble the injector (official assembly; bundles take over after restart)
dsh plugin --profile web add .\injector

# Step 2: install the router presets (one or both)
$target = Join-Path $env:USERPROFILE '.dsh\.agent-presets\router-standard'
Copy-Item -Recurse .\preset\preset $target

# Step 3: restart DSH → pick Router Standard / Router Spec in a new session

# Step 4 (optional): bring up the AI k8s cluster network (cross-platform, Python 3.9+)
bash cluster/scripts/install.sh
aikube cluster init --name demo
aikube run "Design a microservice rollout plan"   # auto-classified → routed to a Pro node
```

## Components

| Path | Repo | Version | Role |
|---|---|---|---|
| `injector/` | [dsh-super-injector](https://github.com/yjh051108/dsh-super-injector) | [v0.3.3](https://github.com/yjh051108/dsh-super-injector/releases/tag/v0.3.3) | Runtime injector: dev_* tool family (inject / hot-reload / staging-promote / uninject / route self-heal) |
| `preset/` | vendored copy (orig. [dsh-router-standard](https://github.com/yjh051108/dsh-router-standard) @ eff787e9) | v0.3.0 + fix | Reasoning-mode routing presets: router-standard / router-spec / router-pro; **includes the extractText import fix** (see [BUG-REPORT.md](BUG-REPORT.md)) |
| `cluster/` | in-repo (can be split out later) | v0.1.1 | AI k8s cluster scheduling network: ai-apiserver / ai-etcd / ai-scheduler / ai-controller / ai-kubelet + `aikube` CLI — pure Python stdlib |

> `preset/` was converted from a submodule into in-repo files (vendored fixed
> copy: eff787e9 tree + extractText fix; patch: [fix-extractText-import.patch](fix-extractText-import.patch))
> because upstream (yjh051108/dsh-router-standard) is not writable and the fix
> needed to ship now. Restore the submodule form once upstream access exists
> (see the push chain in BUG-REPORT.md). `injector/` remains a submodule.

`injector` evolves independently (submodule); `preset` and `cluster` live in
this repo. All three share the same "task-aware routing" idea: the presets
route reasoning modes inside a session; the cluster routes tasks across nodes.

## router-standard preset capabilities (P1–P23 measured summary)

- **Three behavior bands + weak internal routing**: spec (plan-collective) / react (doer) / mixed (trap, avoided) / weak (model self-classifies)
- **Model-matched persona**: Pro = spec sentence + few-shot (discrimination +5.0); Flash = neutral + classify (+5.7)
- **Near-field guidance**: fixed guidance after every real user message (cache 92–94% hit), routing 96% + convergence 100% + anti-dilution
- **Single-task three anchors** (persona-static): recall + converge + anti-runaway — open-task completion 0% → 100%
- **plan-mode preserved**: only the persona section is replaced; plan boundaries never lose focus
- **AI self-optimization tools**: `dev_router_status` / `dev_router_mode` / `dev_mode_subagent`

## Router Pro (v0.3.0) highlights

- V4 Pro measured-optimal routing: maintenance → RL interface (anchored-standard 98/99), build → doer (Mario 10/10), no-evidence → weak (router-v2 few-shot, discrimination +2.6 n=10)
- **Decision-closure loop** (all-branch near-field guidance): black-hole reasoning 58K→27K (2.1× curbed) with 100% action — **no budget cap**
- Competition band [0.03, 0.455] never touched (E2 matrix: 9/12 anti-routing, peak −10.6)
- Model split: Pro = router-v2 few-shot + decision closure; Flash = w7 + commit guidance

## Docs

- Injector guide (10 rules): `injector/README.md`
- Routing preset paper & experiments: `preset/docs/paper.md` + `preset/docs/experiments.md` (P1–P23)
- AI cluster component: `cluster/README.md` + `cluster/docs/architecture.md` (k8s mapping)
  + `cluster/docs/paper.md` (scheduling algorithm) + `cluster/docs/experiments.md` (P1–P3)
- One-shot demo: `bash cluster/scripts/demo.sh`

## License

MIT. Credits: xiaobright/modeltest (V4.1b evaluation), xiaobright/dsh-anchored-standard (anchoring mechanism).

