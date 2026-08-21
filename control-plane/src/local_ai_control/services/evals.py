"""Small deterministic synthetic Golden Set runner; it never calls a provider."""
import json
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class EvalResult:
    case_id: str; passed: bool; reason: str

def run_golden(path: Path, evaluator) -> tuple[EvalResult, ...]:
    data = json.loads(path.read_text())
    results = []
    for case in data["core_cases"]:
        passed = bool(evaluator(case))
        results.append(EvalResult(case["id"], passed, "matched" if passed else "expectation failed"))
    return tuple(results)

def promotion_allowed(results):
    return bool(results) and all(result.passed for result in results)
