"""Descriptive aggregation for synthetic profile experiments; no model calls."""

import statistics


def summarize(rows: list[dict], expected_by_arm: int) -> dict:
    arms = {}
    for arm in ("codex_direct", "langmem_profile"):
        selected = [row for row in rows if row["arm"] == arm]
        completed = [row for row in selected if row["status"] == "ok"]
        calls = [call for row in selected for call in row.get("calls", [])]
        metrics = {}
        for name in ("field", "probe", "preservation"):
            numerator = sum(row["score"][f"{name}_numerator"] for row in completed)
            denominator = sum(row["score"][f"{name}_denominator"] for row in completed)
            metrics[name] = {
                "passed": numerator,
                "total": denominator,
                "rate": numerator / denominator if denominator else None,
            }
        usage = {}
        for call in calls:
            for key, value in (call.get("usage") or {}).items():
                if isinstance(value, (int, float)):
                    usage[key] = usage.get(key, 0) + value
        groups = {}
        for row in selected:
            groups.setdefault((row["case_id"], row["repetition"]), []).append(row)
        arms[arm] = {
            "expected_turns": expected_by_arm,
            "completed_turns": len(completed),
            "errors": sum(row["status"] == "error" for row in selected),
            "skipped_after_error": sum(row["status"] == "skipped" for row in selected),
            "exact_matches": sum(row["score"]["exact_match"] for row in completed),
            "correct_state_delivery_rate": (
                sum(row["score"]["exact_match"] for row in completed) / expected_by_arm if expected_by_arm else None
            ),
            "exact_rate_completed": (
                sum(row["score"]["exact_match"] for row in completed) / len(completed) if completed else None
            ),
            "all_turns_correct_chains": sum(
                all(row["status"] == "ok" and row["score"]["exact_match"] for row in group) for group in groups.values()
            ),
            "chains": len(groups),
            "invariant_violation_turns": sum(bool(row["score"]["invariant_violations"]) for row in completed),
            "metrics_on_completed_turns": metrics,
            "model_calls": len(calls),
            "usage_reported_by_cli": usage,
            "median_turn_seconds": (
                statistics.median(row["elapsed_seconds"] for row in completed) if completed else None
            ),
            "observed_model_snapshots": sorted({c["model_observed"] for c in calls if c.get("model_observed")}),
            "contaminated_calls": sum(bool(c.get("contaminated")) for c in calls),
        }
    return {
        "provenance": "synthetic_development_pilot",
        "comparison_complete": all(arm["completed_turns"] == expected_by_arm for arm in arms.values()),
        "arms": arms,
        "limitations": [
            "Synthetic development cases with explicit reference states; not human labels or hiring outcomes.",
            "Compares direct full-profile generation against real LangMem/Trustcall through the same Codex CLI bridge.",
            "Does not evaluate native Codex background Memories or a native provider tool-calling transport.",
            "Only previous predicted profile and new input cross turns; probes are deterministic state reads, not LLM QA.",
            "Repeated turns within a scenario are dependent; one small pilot does not establish statistical superiority.",
            "CLI model selector is recorded; exact backend snapshot is unknown unless explicitly reported.",
            "CLI read-only mode is not a read-isolation boundary; any observed external tool use invalidates that call.",
            "No price is inferred from token counts; timing includes CLI startup and concurrent execution.",
        ],
    }


def markdown_report(summary: dict, rows: list[dict], config: dict) -> str:
    lines = [
        "# 用户档案更新对比实验",
        "",
        "合成开发实验：Codex 直接更新完整档案，对比真正的 LangMem Profile 更新。",
        f"共同模型选择：`{config['codex']['model']}`；推理档位：`{config['codex']['reasoning_effort']}`。",
        "",
        "| 指标 | Codex 直接更新 | LangMem Profile |",
        "| --- | ---: | ---: |",
    ]
    direct, langmem = (summary["arms"][key] for key in ("codex_direct", "langmem_profile"))
    for label, values in [
        ("完成轮数", [f"{a['completed_turns']}/{a['expected_turns']}" for a in (direct, langmem)]),
        ("当前档案完全正确", [f"{a['exact_matches']}/{a['completed_turns']}" for a in (direct, langmem)]),
        ("预定轮次中交付正确档案", [f"{a['exact_matches']}/{a['expected_turns']}" for a in (direct, langmem)]),
        ("所有轮次均正确的场景链", [f"{a['all_turns_correct_chains']}/{a['chains']}" for a in (direct, langmem)]),
        ("关联字段冲突轮数", [a["invariant_violation_turns"] for a in (direct, langmem)]),
        ("模型调用数", [a["model_calls"] for a in (direct, langmem)]),
        ("错误 / 后续跳过", [f"{a['errors']} / {a['skipped_after_error']}" for a in (direct, langmem)]),
        ("每轮中位耗时（秒）", [a["median_turn_seconds"] for a in (direct, langmem)]),
    ]:
        lines.append(f"| {label} | {values[0]} | {values[1]} |")
    for name, label in (("field", "顶层字段正确"), ("probe", "只读当前状态检查"), ("preservation", "无关字段保留")):
        values = [a["metrics_on_completed_turns"][name] for a in (direct, langmem)]
        lines.append(
            f"| {label} | {values[0]['passed']}/{values[0]['total']} | {values[1]['passed']}/{values[1]['total']} |"
        )
    lines.extend(["", "## 逐轮失败与差异", ""])
    failures = [r for r in rows if r["status"] != "ok" or not r["score"]["exact_match"]]
    if not failures:
        lines.append("本次已完成的全部轮次与冻结参考档案一致；小规模通过不代表两种方案等价。")
    for row in failures:
        lines.append(
            f"- `{row['arm']}/{row['case_id']}/{row['turn_id']}`：{row.get('error') or row['score']['leaf_differences']}"
        )
    lines.extend(["", "## 用量与解释范围", ""])
    for arm, result in summary["arms"].items():
        lines.append(f"- `{arm}` CLI 报告用量：`{result['usage_reported_by_cli']}`。")
    lines.extend([f"- {item}" for item in summary["limitations"]])
    if not summary["comparison_complete"]:
        lines.extend(["", "**实验未完整运行，不能根据已完成的子集宣布胜者。**"])
    return "\n".join(lines) + "\n"
