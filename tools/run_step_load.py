#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶梯加压（Step-Load）执行封装与拐点判定。

能力：
- 按并发阶梯（如 10→30→50→80→100）逐级对同一 Locust 脚本加压；
- 每级独立输出 Locust CSV 到时间戳子目录，不覆盖历史；
- 解析每级 TPS / RT P95 / 错误率，判定**性能拐点**（TPS 不再随并发增长的级别）；
- 输出阶梯汇总 JSON，并给出拐点结论。

说明：阶梯加压是"找拐点"的核心手段（见 agents/planning_agent.md）。
真实场景请用 --locustfile / --host / --data-file 指向你的脚本与数据。

典型用法：
    uv run python -m tools.run_step_load \\
        --locustfile locustfiles/locust_demo_health_baseline.py \\
        --steps "10,30,50,80" --step-duration 30s --scenario demo_health
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from tools.naming import ensure_unique_path


@dataclass(frozen=True)
class StepResult:
    """单级加压的汇总结果。"""

    users: int
    total_requests: int
    total_failures: int
    error_rate: float
    rps: float
    p95_ms: float
    p99_ms: float
    avg_ms: float


@dataclass(frozen=True)
class StepLoadResult:
    """整个阶梯加压的汇总结果。"""

    scenario: str
    locustfile: str
    host: str
    steps: list[StepResult]
    breakdown_point: int | None = None
    breakout_point: int | None = None
    notes: list[str] = field(default_factory=list)


def _to_float(value: str) -> float:
    """忽略逗号地把字符串转为浮点数。"""

    try:
        return float(value.replace(",", "").strip())
    except ValueError:
        return 0.0


def parse_locust_stats_csv(stats_csv: Path) -> StepResult:
    """解析 Locust `stats_stats.csv` 总览行的核心指标。

    兼容 Locust 新旧两套列名：
    - 旧版：`# requests`, `# fails`, `Median`, `95%ile`, `99%ile`, `Average`, `Current RPS`
    - 新版：`Request Count`, `Failure Count`, `Median Response Time`,
            `95%`, `99%`, `Average Response Time`, `Requests/s`
    取“总请求数、失败数、P95、P99、平均 RT、RPS”关键项。
    """

    with stats_csv.open(encoding="utf-8") as f:
        header = f.readline().strip().split(",")
        data = f.readline().strip().split(",")
        # 其余行不重要，只要总览第一行数据即可

    def col(*names: str) -> str:
        """按列名逐一匹配（忽略末尾空白），返回首个有值且下标存在的列值。"""

        for idx, head in enumerate(header):
            head_stripped = head.strip()
            if head_stripped in names and idx < len(data):
                return data[idx].strip()
        # 兼容以数字百分位（如 95%）作为列名的情况
        for idx, head in enumerate(header):
            head_stripped = head.strip().lower()
            for name in names:
                if head_stripped == name.lower() and idx < len(data):
                    return data[idx].strip()
        return "0"

    total_requests = int(_to_float(col("# requests", "Request Count", "requests")))
    total_failures = int(_to_float(col("# fails", "Failure Count", "failures")))
    rps_value = col("Current RPS", "Requests/s", "rps")
    return StepResult(
        users=0,  # 由调用方按当前阶梯填充
        total_requests=total_requests,
        total_failures=total_failures,
        error_rate=(total_failures / total_requests) if total_requests else 0.0,
        rps=_to_float(rps_value),
        p95_ms=_to_float(col("95%ile", "95%")),
        p99_ms=_to_float(col("99%ile", "99%")),
        avg_ms=_to_float(col("Average", "Average Response Time", "avg")),
    )


def detect_breakdown_point(results: list[StepResult], tolerance: float = 0.02) -> int | None:
    """判定性能拐点（TPS 几乎不再随并发增长的阶梯并发数）。

    遍历相邻两级：若后一级并发升高但 TPS 增长比例 ≤ tolerance（默认 2%），
    则认为前一级并发已达拐点水位，返回该并发数；找不到则返回 None。
    """

    for idx in range(len(results) - 1):
        cur, nxt = results[idx], results[idx + 1]
        if cur.rps <= 0:
            continue
        growth = (nxt.rps - cur.rps) / cur.rps
        if nxt.users > cur.users and growth <= tolerance:
            return cur.users
    return None


def detect_breakout_point(results: list[StepResult], error_threshold: float = 0.05) -> int | None:
    """判定击穿点：错误率首次超过阈值的阶梯并发数。"""

    for r in results:
        if r.error_rate > error_threshold:
            return r.users
    return None


def run_step_load(
    *,
    locustfile: str,
    steps: list[int],
    step_duration: str,
    scenario: str,
    host: str,
    path: str,
    data_file: str,
    raw_base: Path = Path("reports/raw"),
    abort_error_threshold: float = 0.30,
    max_stderr_head: int = 200,
) -> StepLoadResult:
    """逐级执行 Locust 阶梯加压并汇总结果。

    每级写入 raw_base/<scenario>/<ts>/step_<并发>_users/stats_stats.csv，
    带时间戳子目录避免覆盖。
    """

    ts = os.getenv("LOCUST_RUN_TIMESTAMP")
    if not ts:
        from tools.naming import timestamp as _ts

        ts = _ts()

    notes: list[str] = []
    step_results: list[StepResult] = []
    env = os.environ.copy()
    env.setdefault("LOCUST_TARGET_HOST", host)
    if path:
        env["LOCUST_STEP_LOAD_PATH"] = path
    if data_file:
        env["LOCUST_STEP_LOAD_DATA_FILE"] = data_file

    for users in steps:
        step_dir = ensure_unique_path(raw_base / scenario / ts / f"step_{users}_users")
        step_dir.mkdir(parents=True, exist_ok=True)

        command = [
            sys.executable,
            "-m",
            "locust",
            "-f",
            locustfile,
            "--headless",
            "-u",
            str(users),
            "-r",
            str(max(1, users // 5)),
            "-t",
            step_duration,
            "--csv",
            str(step_dir / "stats"),
        ]
        print(f"[step-load] 执行阶梯 users={users} duration={step_duration} dir={step_dir}")
        proc = subprocess.run(command, check=False, env=env)

        stats_csv = step_dir / "stats_stats.csv"
        if not stats_csv.exists():
            raise RuntimeError(
                f"阶梯 {users} 未生成 stats CSV，退出码={proc.returncode}，"
                f"原始目录: {step_dir}"
            )

        result = parse_locust_stats_csv(stats_csv)
        result = StepResult(
            users=users,
            total_requests=result.total_requests,
            total_failures=result.total_failures,
            error_rate=result.error_rate,
            rps=result.rps,
            p95_ms=result.p95_ms,
            p99_ms=result.p99_ms,
            avg_ms=result.avg_ms,
        )
        step_results.append(result)
        print(
            f"  -> rps={result.rps:.1f} p95={result.p95_ms:.0f}ms "
            f"error={result.error_rate:.2%} reqs={result.total_requests}"
        )

        # 熔断：错误率超过 abort 阈值则提前终止后续阶梯
        if result.error_rate > abort_error_threshold:
            notes.append(
                f"阶梯 {users} 错误率 {result.error_rate:.2%} 超过熔断阈值 "
                f"{abort_error_threshold:.0%}，已提前终止。"
            )
            break

    breakdown = detect_breakdown_point(step_results)
    breakout = detect_breakout_point(step_results)
    if breakdown is not None:
        notes.append(f"性能拐点：并发 {breakdown} 时 TPS 不再随并发增长。")
    if breakout is not None:
        notes.append(f"击穿点：并发 {breakout} 时错误率超过 5%。")
    if breakdown is None and breakout is None:
        notes.append("未检测到明确拐点/击穿，可能尚未达到系统的容量上限，建议继续加压。")

    return StepLoadResult(
        scenario=scenario,
        locustfile=locustfile,
        host=host,
        steps=step_results,
        breakdown_point=breakdown,
        breakout_point=breakout,
        notes=notes,
    )


def main() -> None:
    """命令行入口。"""

    parser = argparse.ArgumentParser(description="Run step-load stress and detect breakdown point.")
    parser.add_argument("--locustfile", required=True, help="Locust 脚本路径，如 locustfiles/xxx.py")
    parser.add_argument("--steps", required=True, help="并发阶梯，逗号分隔，如 10,30,50,80,100")
    parser.add_argument("--step-duration", default="30s", help="每级持续时长，如 30s、1m")
    parser.add_argument("--scenario", default="step_load", help="场景名")
    parser.add_argument("--host", default="https://example.com", help="目标 host")
    parser.add_argument("--path", default="", help="接口路径（可选，透传给脚本）")
    parser.add_argument("--data-file", default="", help="数据文件路径（可选）")
    parser.add_argument("--abort-error-threshold", type=float, default=0.30, help="错误率熔断阈值，默认 0.30")
    parser.add_argument("--summary-dir", default="reports/raw", help="原始产物根目录")
    args = parser.parse_args()

    steps = [int(s.strip()) for s in args.steps.split(",") if s.strip()]
    if not steps:
        parser.error("--steps 至少要一个整数值")
    if any(s <= 0 for s in steps):
        parser.error("--steps 中的并发数必须为正数")

    result = run_step_load(
        locustfile=args.locustfile,
        steps=steps,
        step_duration=args.step_duration,
        scenario=args.scenario,
        host=args.host,
        path=args.path,
        data_file=args.data_file,
        raw_base=Path(args.summary_dir),
        abort_error_threshold=args.abort_error_threshold,
    )

    print("\n==== 阶梯加压汇总 ====")
    header = f"{'并发':>5} | {'RPS':>8} | {'P95(ms)':>8} | {'错误率':>8} | {'请求数':>8}"
    print(header)
    print("-" * len(header))
    for r in result.steps:
        print(
            f"{r.users:>5} | {r.rps:>8.1f} | {r.p95_ms:>8.0f} | "
            f"{r.error_rate:>7.2%} | {r.total_requests:>8}"
        )
    if result.breakdown_point is not None:
        print(f"\n> 性能拐点：并发 {result.breakdown_point}")
    if result.breakout_point is not None:
        print(f"> 击穿点：并发 {result.breakout_point}")
    for note in result.notes:
        print(f"- {note}")

    # 落盘汇总 JSON
    out = ensure_unique_path(Path(args.summary_dir) / args.scenario / "step_load_summary.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "scenario": result.scenario,
        "locustfile": result.locustfile,
        "host": result.host,
        "breakdown_point": result.breakdown_point,
        "breakout_point": result.breakout_point,
        "notes": result.notes,
        "steps": [asdict(r) for r in result.steps],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n阶梯汇总已写入: {out}")


if __name__ == "__main__":
    main()