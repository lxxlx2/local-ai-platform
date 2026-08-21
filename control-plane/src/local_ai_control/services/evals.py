"""Small deterministic synthetic Golden Set runner; it never calls a provider."""
import json
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class EvalResult:
    case_id: str; passed: bool; reason: str

def run_golden(path: Path, evaluator) -> tuple[EvalResult, ...]:
    data = json.loads(path.read_text())
    return tuple(EvalResult(case["id"], bool(evaluator(case)), "matched" if evaluator(case) else "expectation failed") for case in data["core_cases"])

def promotion_allowed(results):
    return bool(results) and all(result.passed for result in results)
