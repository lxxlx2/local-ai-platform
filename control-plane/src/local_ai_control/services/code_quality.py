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
    docstrings = "\n".join(
        doc
        for node in (tree, *ast.walk(tree))
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        if (doc := ast.get_docstring(node, clean=False))
    )
    documents_required_level_as_string = bool(
        re.search(r"required_level\s*(?:\(\s*str\s*\)|:\s*str\b)", docstrings, flags=re.I)
    )
    def numeric_constant(node):
        return isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool)

    uses_numeric_required_level = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "require_level":
            positional_numeric = bool(node.args) and numeric_constant(node.args[0])
            keyword_numeric = any(keyword.arg == "required_level" and numeric_constant(keyword.value) for keyword in node.keywords)
            uses_numeric_required_level = uses_numeric_required_level or positional_numeric or keyword_numeric
        if isinstance(node, ast.Compare):
            operands = (node.left, *node.comparators)
            has_required_level = any(isinstance(operand, ast.Name) and operand.id == "required_level" for operand in operands)
            uses_numeric_required_level = uses_numeric_required_level or (has_required_level and any(numeric_constant(operand) for operand in operands))
    if documents_required_level_as_string and uses_numeric_required_level:
        return PythonBlockCheck(True, False, "required_level documented as str but used as numeric")
    return PythonBlockCheck(True, True)
