#!/usr/bin/env python3
"""promptfoo 结果汇总（v0.122 结构）：通过率 + 端到端延迟 P50/P95。"""
import json
import statistics
import sys


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "results.json"
    data = json.load(open(path, encoding="utf-8"))
    inner = data.get("results", data)
    if isinstance(inner, dict):
        inner = inner.get("results", [])
    latencies = []
    passed = 0
    total = len(inner)
    per_case = {}
    for r in inner:
        prompt = r.get("prompt", {})
        task = (prompt.get("raw") if isinstance(prompt, dict) else str(prompt))[:26]
        ok = bool(r.get("gradingResult", {}).get("pass"))
        passed += 1 if ok else 0
        lat = r.get("latencyMs")
        if isinstance(lat, (int, float)):
            latencies.append(lat)
            per_case.setdefault(task, []).append(lat)
        print(f"  {'PASS' if ok else 'FAIL'}  {task:<28} "
              f"{lat if lat is not None else '-':>8.0f} ms")
    print("-" * 60)
    print(f"通过率: {passed}/{total}")
    if latencies:
        latencies.sort()
        p50 = statistics.median(latencies)
        p95 = latencies[int(len(latencies) * 0.95) - 1]
        print(f"端到端延迟: P50 = {p50:.0f} ms, P95 = {p95:.0f} ms, "
              f"min = {min(latencies):.0f} ms, max = {max(latencies):.0f} ms")
        print("分用例:")
        for name, lats in per_case.items():
            lats.sort()
            print(f"  {name:<28} P50={statistics.median(lats):.0f} "
                  f"P95={lats[int(len(lats) * 0.95) - 1]:.0f} ms (n={len(lats)})")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
