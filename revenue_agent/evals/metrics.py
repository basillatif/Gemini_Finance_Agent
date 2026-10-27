"""Deterministic custom metrics for `adk eval`, wired up in test_config.json.

Each function scores every invocation 1.0 (pass) or 0.0 (fail) and passes a
case only if all its invocations pass. No LLM is involved, so these are free
to run and give the same answer every time.

- tool_args_match: the expected tool calls happened, in order, with the
  expected arguments (extra calls are allowed; empty-string args are ignored).
- required_figures: every figure in the answer key appears in the response.
- grounded_figures: every dollar, percent, or unit figure in the response
  can be traced to a tool result (the "never invent numbers" rule).
"""
import re
from decimal import Decimal

from google.adk.evaluation.eval_case import get_all_tool_calls, get_all_tool_responses
from google.adk.evaluation.evaluator import EvalStatus, EvaluationResult, PerInvocationResult

_DATE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b|\bQ[1-4]\s*\d{4}\b|\b\d{4}-Q[1-4]\b")
_NUMBER = re.compile(r"(\$)?\s?(-?\d[\d,]*(?:\.\d+)?)\s*([KMB](?![a-z])|million|billion|thousand)?\s*(%|\s?pts?\b|\s?percentage points?)?", re.I)
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}
_YEARS = range(1990, 2101)


def _text(content) -> str:
    return "".join(p.text or "" for p in (content.parts or [])) if content else ""


def _figures(text: str) -> list[tuple[str, float, float]]:
    """Returns (kind, value, tolerance) for each money, percent, or count figure in text."""
    out = []
    for dollar, num, scale, pct in _NUMBER.findall(_DATE.sub(" ", text)):
        value = float(num.replace(",", ""))
        if pct:
            decimals = len(num.split(".")[1]) if "." in num else 0
            out.append(("pct", abs(value), 0.05 if decimals else 0.5))
        elif dollar or scale:
            unit = _SCALE.get(scale.lower(), 1) if scale else 1
            # "$2.86M" is accurate to half of its last digit; "$2,864,084" to rounding.
            last_digit = 10 ** -len(num.split(".")[1]) if "." in num else 1
            out.append(("num", abs(value) * unit, max(1.0, last_digit * unit / 2)))
        elif value >= 1000 and value == int(value) and int(value) not in _YEARS:
            # Bare counts such as units ("8,003" or "8003"); four-digit years are skipped.
            out.append(("num", abs(value), 1.0))
    return out


def _tool_values(response: dict) -> list[float]:
    """Flattens every number in a tool result."""
    values = []

    def walk(v):
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float, Decimal)):
            values.append(abs(float(v)))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(response)
    return values


def _result(scores: list[float], actual, expected) -> EvaluationResult:
    per = [
        PerInvocationResult(
            actual_invocation=a,
            expected_invocation=e,
            score=s,
            eval_status=EvalStatus.PASSED if s == 1.0 else EvalStatus.FAILED,
        )
        for s, a, e in zip(scores, actual, expected or [None] * len(actual))
    ]
    overall = sum(scores) / len(scores) if scores else None
    return EvaluationResult(
        overall_score=overall,
        overall_eval_status=EvalStatus.PASSED if overall == 1.0 else EvalStatus.FAILED,
        per_invocation_results=per,
    )


def _normalize(args: dict) -> dict:
    return {k: v for k, v in (args or {}).items() if v not in ("", None)}


def tool_args_match(eval_metric, actual_invocations, expected_invocations, conversation_scenario=None):
    scores = []
    for actual, expected in zip(actual_invocations, expected_invocations):
        remaining = [(c.name, _normalize(c.args)) for c in get_all_tool_calls(actual.intermediate_data)]
        ok = True
        for want in get_all_tool_calls(expected.intermediate_data):
            target = (want.name, _normalize(want.args))
            if target in remaining:
                remaining = remaining[remaining.index(target) + 1:]
            else:
                ok = False
                break
        scores.append(1.0 if ok else 0.0)
    return _result(scores, actual_invocations, expected_invocations)


def required_figures(eval_metric, actual_invocations, expected_invocations, conversation_scenario=None):
    scores = []
    for actual, expected in zip(actual_invocations, expected_invocations):
        got = _figures(_text(actual.final_response))
        want = _figures(_text(expected.final_response))
        hits = sum(
            any(gk == wk and abs(gv - wv) <= max(gt, wt) for gk, gv, gt in got)
            for wk, wv, wt in want
        )
        scores.append(hits / len(want) if want else 1.0)
    return _result(scores, actual_invocations, expected_invocations)


def grounded_figures(eval_metric, actual_invocations, expected_invocations, conversation_scenario=None):
    scores = []
    for actual, expected in zip(actual_invocations, expected_invocations or [None] * len(actual_invocations)):
        known = [v for r in get_all_tool_responses(actual.intermediate_data) for v in _tool_values(r.response)]
        known += [v for _, v, _ in _figures(_text(actual.user_content))]
        got = _figures(_text(actual.final_response))
        grounded = sum(any(abs(v - k) <= tol for k in known) for _, v, tol in got)
        scores.append(grounded / len(got) if got else 1.0)
    return _result(scores, actual_invocations, expected_invocations)
