import ast
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PythonBlockCheck:
    syntax_valid: bool
    standalone_claim_ok: bool
    issue: str | None = None


def python_blocks(markdown: str):
    return re.findall(r"```python\s*\n([\s\S]*?)```", markdown, flags=re.I)


def check_python_block(source: str) -> PythonBlockCheck:
    try:
        ast.parse(source)
    except SyntaxError as error:
        return PythonBlockCheck(False, False, f"syntax:{error.msg}")
    uses_functools = "functools." in source
    imports_functools = bool(re.search(r"^\s*(?:import functools|from functools import )", source, flags=re.M))
    if uses_functools and not imports_functools:
        return PythonBlockCheck(True, False, "functools import missing")
    return PythonBlockCheck(True, True)
