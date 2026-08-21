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
    tree = ast.parse(source)
    imported, defined = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            defined.add(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(node.name)
            defined.update(arg.arg for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs))
            if node.args.vararg: defined.add(node.args.vararg.arg)
            if node.args.kwarg: defined.add(node.args.kwarg.arg)
        elif isinstance(node, ast.Import):
            imported.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
    known = imported | defined | set(dir(__builtins__))
    # This is intentionally a lightweight static gate, not an execution engine.
    # Attribute roots are a reliable signal for missing module imports; plain
    # callback names such as ``func`` in a decorator wrapper may be supplied by
    # the surrounding decorator contract and should not be guessed as imports.
    missing = sorted({node.value.id for node in ast.walk(tree) if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id not in known})
    if missing:
        name = missing[0]
        return PythonBlockCheck(True, False, "functools import missing" if name == "functools" else f"unresolved name: {name}")
    return PythonBlockCheck(True, True)
