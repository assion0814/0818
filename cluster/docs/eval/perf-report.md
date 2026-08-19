# AI 集群调度 性能门禁报告（promptfoo）

- 工具: promptfoo 0.122.0（custom provider `aikube-provider.mjs`）
- 度量: 端到端延迟 = 提交任务 → 全部 Pod 终态（真实集群 ~/.aikube，1 主 2 从，mock 运行时）
- 用例: 5 任务 × repeat 2（演示规模；正式门禁建议 repeat ≥ 10）

## 结果

| 指标 | 值 |
|---|---|
| 通过率 | **10/10 (100%)**（断言：全部 Pod Succeeded） |
| P50 | 3330 ms |
| P95 | 5468 ms |
| min / max | 2255 / 6540 ms |

分用例 P50（n=2，仅供参考）：

| 任务 | P50 |
|---|---|
| 设计一个微服务架构上线方案 | 2819 ms |
| 修复登录页面的 bug | 2852 ms |
| 用 GPU 训练图像识别模型 | 2808 ms |
| 重构支付模块并输出完整实施方案 | 2786 ms |
| 把用户反馈整理成一份报告文档 | 6004 ms（文档类任务执行路径更长，待查） |

## 复现

```bash
cd cluster/perf
npx promptfoo eval --config promptfooconfig.yaml --repeat 10 --output results.json
python3 summary.py results.json
```

原始数据: `perf/results.json` / `docs/eval/perf-results.json`
