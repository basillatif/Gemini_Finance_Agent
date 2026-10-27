"""Summarize an `adk eval` result file as a Markdown table plus failure details.

    ../.venv/bin/python evals/summarize.py [RESULT_FILE]     (from revenue_agent/)

With no argument, reads the newest file in revenue_agent/.adk/eval_history/.
"""
import json
import sys
from pathlib import Path

HISTORY = Path(__file__).parent.parent / "revenue_agent" / ".adk" / "eval_history"
METRICS = ["tool_args_match", "required_figures", "grounded_figures", "rubric_based_final_response_quality_v1"]
SHORT = {"tool_args_match": "tools", "required_figures": "figures", "grounded_figures": "grounded",
         "rubric_based_final_response_quality_v1": "rubric"}
STATUS = {1: "pass", 2: "FAIL", 3: "n/a"}


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else max(HISTORY.glob("*.json"), key=lambda p: p.stat().st_mtime)
    data = json.loads(path.read_text())
    if isinstance(data, str):
        data = json.loads(data)
    cases = sorted(data["eval_case_results"], key=lambda c: c["eval_id"])

    print(f"Result file: {path.name}\n")
    print("| Case | " + " | ".join(SHORT[m] for m in METRICS) + " | Overall |")
    print("|---|" + "---|" * (len(METRICS) + 1))
    scored = {m: [0, 0] for m in METRICS}
    passed = 0
    for c in cases:
        by_name = {r["metric_name"]: r for r in c["overall_eval_metric_results"]}
        cells, ok = [], True
        for m in METRICS:
            r = by_name.get(m)
            status = STATUS.get(r["eval_status"], "n/a") if r else "n/a"
            if status != "n/a":
                scored[m][0] += status == "pass"
                scored[m][1] += 1
                ok &= status == "pass"
            cells.append(status)
        passed += ok
        print(f"| {c['eval_id']} | " + " | ".join(cells) + f" | {'pass' if ok else 'FAIL'} |")

    print(f"\nCases passing every scored metric: {passed}/{len(cases)} ({passed / len(cases):.0%})")
    for m in METRICS:
        p, n = scored[m]
        print(f"- {SHORT[m]}: {p}/{n}" if n else f"- {SHORT[m]}: not scored")

    print("\n## Failure details\n")
    for c in cases:
        failed = [r["metric_name"] for r in c["overall_eval_metric_results"] if r["eval_status"] == 2]
        if not failed:
            continue
        print(f"### {c['eval_id']} (failed: {', '.join(SHORT.get(m, m) for m in failed)})")
        for inv in c["eval_metric_result_per_invocation"]:
            actual, expected = inv["actual_invocation"], inv["expected_invocation"]
            print(f"- Q: {actual['user_content']['parts'][0]['text']}")
            calls = _calls(actual)
            print(f"- Tool calls: {calls or 'none'}")
            want = _calls(expected)
            if want:
                print(f"- Expected calls: {want}")
            text = "".join(p.get("text") or "" for p in (actual.get("final_response") or {}).get("parts", []))
            print("- Answer:\n\n  > " + text.strip().replace("\n", "\n  > ") + "\n")


def _calls(invocation):
    data = invocation.get("intermediate_data") or {}
    if "tool_uses" in data:
        uses = data["tool_uses"]
    else:
        uses = [p["function_call"] for e in data.get("invocation_events", [])
                for p in (e.get("content") or {}).get("parts", []) if p.get("function_call")]
    return [f"{u['name']}({', '.join(f'{k}={v!r}' for k, v in (u.get('args') or {}).items() if v != '')})" for u in uses]


if __name__ == "__main__":
    main()
