"""Benchmark disruption extraction (and Jev severity) on labelled report sets.

    python -m app.services.llm.evaluate                          # configured LLM vs rules, dev set
    python -m app.services.llm.evaluate --set heldout            # the original 5 samples
    python -m app.services.llm.evaluate --provider openrouter \
        --models nvidia/nemotron-3-super-120b-a12b:free,qwen/qwen3.8-27b:free
    python -m app.services.llm.evaluate --jev                    # also score Jev severity

Labelled files are JSON lists of {text, expected_affected_lines, expected_affected_stops,
expected_effect, expected_start, expected_end, expected_severity}; missing labels are skipped.

Per system it reports:
  success_ratio   share of reports the model itself answered (no rule-based fallback)
  fully_correct   share of reports with lines, stops, effect and times all right
  lines / stops   micro precision, recall, F1
  effect_acc, time_acc, latency_p50_s
"""

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any

from app.config import DATA_DIR
from app.services.decisions import classify_disruption_severity, get_jev_client
from app.services.disruptions import normalize_stop
from app.services.llm.client import LLMClient, LLMError, QuotaExhaustedError, make_llm_client
from app.services.llm.extraction import DisruptionExtraction, extract_disruption, extract_with_rules
from app.services.routing.network import normalize_line

SETS = {
    "dev": DATA_DIR / "eval" / "disruptions_dev.json",
    "heldout": DATA_DIR / "sample_disruptions" / "disruptions.json",
}
# OpenRouter's free-model limit (~20 requests/minute) is per account, across all models,
# so every LLM call in a benchmark goes through one shared pacer.
MIN_INTERVAL_S = 3.5
PAID_INTERVAL_S = 0.5  # Paid models have no shared daily/minute limit
CALL_TIMEOUT_S = 90.0  # A call that never returns counts as a wrong answer, not a hung run


FAILED = DisruptionExtraction(
    summary="",
    affected_lines=[],
    affected_stops=[],
    start_time=None,
    end_time=None,
    duration_unknown=True,
    disruption_type="other",
    effect="other",
    delay_minutes=None,
    alternatives=[],
    method="error",
)


class _Pacer:
    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self) -> None:
        async with self._lock:
            delay = self._next - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next = time.monotonic() + self.interval


def _prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    return {
        "precision": round(p, 3),
        "recall": round(r, 3),
        "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0,
    }


def _stop_match(pred: str, gold: str) -> bool:
    a, b = normalize_stop(pred), normalize_stop(gold)
    return bool(a) and (a == b or a in b or b in a)


def score_case(case: dict[str, Any], ext: Any) -> dict[str, Any]:
    """Compare one extraction with its labels."""
    gold_lines = {normalize_line(x) for x in case.get("expected_affected_lines", [])}
    pred_lines = {normalize_line(x) for x in ext.affected_lines}
    gold_stops = case.get("expected_affected_stops", [])
    matched = [g for g in gold_stops if any(_stop_match(p, g) for p in ext.affected_stops)]
    extra = [p for p in ext.affected_stops if not any(_stop_match(p, g) for g in gold_stops)]
    effect_ok = ext.effect == case["expected_effect"] if "expected_effect" in case else None
    times_ok = (
        (ext.start_time, ext.end_time) == (case["expected_start"], case["expected_end"])
        if "expected_start" in case
        else None
    )
    lines_ok = pred_lines == gold_lines
    stops_ok = len(matched) == len(gold_stops) and not extra
    return {
        "lines": (
            len(gold_lines & pred_lines),
            len(pred_lines - gold_lines),
            len(gold_lines - pred_lines),
        ),
        "stops": (len(matched), len(extra), len(gold_stops) - len(matched)),
        "effect_ok": effect_ok,
        "times_ok": times_ok,
        "fully_correct": lines_ok and stops_ok and effect_ok is not False and times_ok is not False,
    }


async def run_system(
    name: str, client: Any, cases: list[dict[str, Any]], pacer: _Pacer
) -> dict[str, Any]:
    scores, latencies, details = [], [], []
    quota_hit = False
    for case in cases:
        if client is None:
            ext = extract_with_rules(case["text"])
        else:
            await pacer.wait()
            started = time.monotonic()
            try:
                ext = await asyncio.wait_for(
                    extract_disruption(client, case["text"], fallback=False), CALL_TIMEOUT_S
                )
                latencies.append(time.monotonic() - started)
            except QuotaExhaustedError:
                quota_hit = True  # Stop: everything after this would be a quota error
                break
            except (LLMError, ValueError, TypeError, TimeoutError):
                ext = FAILED  # A failed call is scored as an empty (wrong) answer
        s = score_case(case, ext)
        scores.append(s)
        details.append(
            {
                "text": case["text"][:70],
                "method": ext.method,
                "answered_by": getattr(client, "last_model", None)
                if ext.method == "llm"
                else "rules",
                "effect": ext.effect,
                "lines": ext.affected_lines,
                "stops": ext.affected_stops,
                "times": [ext.start_time, ext.end_time],
                "fully_correct": s["fully_correct"],
            }
        )

    def total(key: str) -> tuple[int, int, int]:
        return tuple(sum(s[key][i] for s in scores) for i in range(3))  # type: ignore[return-value]

    def accuracy(key: str) -> float | None:
        vals = [s[key] for s in scores if s[key] is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    n = len(details)
    if n == 0:
        return {"system": name, "reports": 0, "quota_exhausted": quota_hit, "cases": []}
    success = sum(d["method"] == "llm" for d in details) / n if client is not None else None
    return {
        "system": name,
        "reports": n,
        "quota_exhausted": quota_hit,
        "success_ratio": None if success is None else round(success, 3),
        "fully_correct": round(sum(s["fully_correct"] for s in scores) / n, 3),
        "lines": _prf(*total("lines")),
        "stops": _prf(*total("stops")),
        "effect_acc": accuracy("effect_ok"),
        "time_acc": accuracy("times_ok"),
        "latency_p50_s": round(statistics.median(latencies), 1) if latencies else None,
        "cases": details,
    }


async def jev_severity(cases: list[dict[str, Any]]) -> dict[str, Any] | None:
    jev = get_jev_client()
    if jev is None:
        return None
    correct, rows = 0, []
    for case in cases:
        decision = await classify_disruption_severity(jev, case["text"])
        predicted = decision.get_choice("severity")
        # Labels use low/medium/high; count Jev's "critical" as high
        ok = ("high" if predicted == "critical" else predicted) == case.get("expected_severity")
        correct += ok
        answer = decision.get_answer("severity")
        rows.append(
            {
                "text": case["text"][:70],
                "expected": case.get("expected_severity"),
                "jev": predicted,
                "confidence": answer.confidence if answer else None,
            }
        )
    return {"accuracy": round(correct / len(cases), 3), "cases": rows}


async def evaluate(
    cases: list[dict[str, Any]],
    provider: str | None = None,
    models: list[str] | None = None,
    use_llm: bool = True,
    with_jev: bool = False,
) -> dict[str, Any]:
    systems: dict[str, Any] = {"rules": None}
    if use_llm:
        if models:
            for m in models:
                systems[m] = LLMClient(provider or "openrouter", model=m)
        else:
            client = make_llm_client()
            if client is not None:
                systems[client.describe()["model"]] = client
    free = _Pacer(MIN_INTERVAL_S)  # Free models share one account-wide limit

    def pacer(name: str) -> _Pacer:
        return free if name.endswith(":free") or name == "rules" else _Pacer(PAID_INTERVAL_S)

    results = await asyncio.gather(*(run_system(n, c, cases, pacer(n)) for n, c in systems.items()))
    for client in systems.values():
        if client is not None:
            await client.close()
    out: dict[str, Any] = {"systems": sorted(results, key=lambda r: -r.get("fully_correct", 0))}
    if with_jev:
        out["jev_severity"] = await jev_severity(cases)
    return out


def print_table(result: dict[str, Any]) -> None:
    def fmt(v: Any) -> str:
        return "-" if v is None else f"{v:.2f}"

    print(
        f"{'system':52} {'n':>3} {'success':>7} {'fully':>6} {'lines':>6} {'stops':>6} "
        f"{'effect':>6} {'times':>6} {'p50 s':>6}"
    )
    for r in result["systems"]:
        if not r["reports"]:
            print(f"{r['system'][:52]:52}   0  daily quota used up before the first report")
            continue
        print(
            f"{r['system'][:52]:52} {r['reports']:>3} {fmt(r['success_ratio']):>7} "
            f"{fmt(r['fully_correct']):>6} "
            f"{fmt(r['lines']['f1']):>6} {fmt(r['stops']['f1']):>6} {fmt(r['effect_acc']):>6} "
            f"{fmt(r['time_acc']):>6} {fmt(r['latency_p50_s']):>6}"
        )
    if any(r.get("quota_exhausted") for r in result["systems"]):
        print("\nDaily quota ran out: rows with n below the set size were cut short.")
    if result.get("jev_severity"):
        print(f"\nJev severity accuracy: {result['jev_severity']['accuracy']:.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--set", choices=[*SETS, "all"], default="dev")
    parser.add_argument("--file", help="Use this labelled file instead of a named set")
    parser.add_argument("--provider", help="Provider for --models (default openrouter)")
    parser.add_argument("--models", help="Comma-separated models to benchmark side by side")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--jev", action="store_true", help="Also score Jev severity")
    parser.add_argument("--out", help="Write full results (per report) as JSON here")
    args = parser.parse_args()

    if args.file:
        cases = json.loads(Path(args.file).read_text())
    else:
        names = list(SETS) if args.set == "all" else [args.set]
        cases = [c for n in names for c in json.loads(SETS[n].read_text())]
    models = [m.strip() for m in args.models.split(",")] if args.models else None
    result = asyncio.run(evaluate(cases, args.provider, models, not args.no_llm, args.jev))
    print_table(result)
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
