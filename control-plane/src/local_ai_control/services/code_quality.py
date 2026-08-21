import ast
import hashlib
import os
import re
import resource
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class PythonBlockCheck:
    syntax_valid: bool
    standalone_claim_ok: bool
    issue: str | None = None


class CodeValidationLevel(str, Enum):
    UNVALIDATED = "UNVALIDATED"
    STATIC_VALIDATED = "STATIC_VALIDATED"
    SANDBOX_EXECUTION_VALIDATED = "SANDBOX_EXECUTION_VALIDATED"


@dataclass(frozen=True)
class CodeValidationResult:
    level: CodeValidationLevel
    issue: str | None = None
    stdout_marker_seen: bool = False
    peak_rss_kib: int | None = None


class SandboxedCodeValidator:
    """Execution interface. Unknown model output must never be executed."""

    def validate_python(self, source: str, fixture_id: str | None = None) -> CodeValidationResult:
        raise NotImplementedError


GOLDEN_CODE_002_SOURCE = '''import functools

def require_admin(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        current_user = kwargs.pop("current_user", "guest")
        if current_user != "admin":
            raise PermissionError("admin required")
        return func(*args, **kwargs)
    return wrapper

@require_admin
def delete_database():
    return "deleted"

@require_admin
def view_profile(name):
    return f"profile:{name}"

try:
    delete_database()
except PermissionError:
    pass
else:
    raise AssertionError("guest path must be denied")

assert delete_database(current_user="admin") == "deleted"
assert view_profile("alice", current_user="admin") == "profile:alice"
print("GOLDEN_CODE_002_PASS")
'''


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
    for function in (node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.args.kwarg):
        kwargs_name = function.args.kwarg.arg

        class FunctionBodyCollector(ast.NodeVisitor):
            def __init__(self, root):
                self.root = root
                self.nodes = []

            def generic_visit(self, node):
                self.nodes.append(node)
                super().generic_visit(node)

            def visit_FunctionDef(self, node):
                if node is self.root:
                    self.generic_visit(node)

            def visit_AsyncFunctionDef(self, node):
                if node is self.root:
                    self.generic_visit(node)

        collector = FunctionBodyCollector(function)
        collector.visit(function)
        node_set = set(collector.nodes)
        parents = {
            child: parent
            for parent in collector.nodes
            for child in ast.iter_child_nodes(parent)
            if child in node_set
        }
        nodes = sorted(
            collector.nodes,
            key=lambda item: (getattr(item, "lineno", -1), getattr(item, "col_offset", -1)),
        )

        constant_values = {}
        symbol_versions = {}

        def constant_key(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.Name):
                return constant_values.get(node.id, f"<dynamic:{node.id}:{symbol_versions.get(node.id, 0)}>")
            return f"<dynamic-expr:{getattr(node, 'lineno', -1)}:{getattr(node, 'col_offset', -1)}>"

        def is_dynamic_key(key):
            return key.startswith("<dynamic:") or key.startswith("<dynamic-expr:")

        # Keys are learned from actual reads in source order. Unknown runtime
        # keys remain tainted rather than being guessed safe.
        control_keys = set()

        # Track mapping identity and missing keys in source order. A direct alias
        # shares identity; copy/dict/unpack creates a snapshot with its own state.
        mapping_ids = {kwargs_name: 0}
        absent_keys = {0: set()}
        next_mapping_id = 1
        uncertain_mapping_names = set()

        conditional_ancestors = (
            ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With,
            ast.AsyncWith, ast.IfExp, ast.BoolOp, ast.comprehension,
        )
        if hasattr(ast, "Match"):
            conditional_ancestors += (ast.Match,)

        def is_unconditional(node):
            parent = parents.get(node)
            while parent is not None and parent is not function:
                if isinstance(parent, conditional_ancestors):
                    return False
                parent = parents.get(parent)
            return True

        def derived_mapping(value):
            if isinstance(value, ast.Name) and value.id in uncertain_mapping_names:
                return "conditional", None
            if isinstance(value, ast.Name) and value.id in mapping_ids:
                return "alias", mapping_ids[value.id]
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and value.func.attr == "copy"
            ):
                source = derived_mapping(value.func.value)
                if source:
                    return ("conditional", None) if source[0] == "conditional" else ("copy", source[1])
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "dict"
                and value.args
            ):
                source = derived_mapping(value.args[0])
                if source:
                    return ("conditional", None) if source[0] == "conditional" else ("copy", source[1])
            if isinstance(value, ast.Dict):
                relationships = [
                    derived_mapping(item)
                    for key, item in zip(value.keys, value.values)
                    if key is None
                ]
                if any(item and item[0] == "conditional" for item in relationships):
                    return "conditional", None
                source_ids = {item[1] for item in relationships if item and item[1] is not None}
                if len(source_ids) == 1:
                    return "copy", next(iter(source_ids))
                if len(source_ids) > 1:
                    return "conditional", None
            if isinstance(value, ast.BinOp) and isinstance(value.op, ast.BitOr):
                relationships = [derived_mapping(value.left), derived_mapping(value.right)]
                if any(item and item[0] == "conditional" for item in relationships):
                    return "conditional", None
                source_ids = {item[1] for item in relationships if item and item[1] is not None}
                if len(source_ids) == 1:
                    return "copy", next(iter(source_ids))
                if len(source_ids) > 1:
                    return "conditional", None
            if isinstance(value, ast.IfExp):
                if derived_mapping(value.body) or derived_mapping(value.orelse):
                    return "conditional", None
            return None

        for node in nodes:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                relationship = derived_mapping(node.value)
                targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
                if is_unconditional(node):
                    for target in targets:
                        if isinstance(target, ast.Name):
                            symbol_versions[target.id] = symbol_versions.get(target.id, 0) + 1
                            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                                constant_values[target.id] = node.value.value
                            else:
                                constant_values.pop(target.id, None)
                else:
                    for target in targets:
                        if isinstance(target, ast.Name):
                            symbol_versions[target.id] = symbol_versions.get(target.id, 0) + 1
                            constant_values.pop(target.id, None)
                if relationship and relationship[0] == "conditional":
                    for target in targets:
                        if isinstance(target, ast.Name):
                            uncertain_mapping_names.add(target.id)
                            mapping_ids.pop(target.id, None)
                elif relationship and is_unconditional(node):
                    kind, source_id = relationship
                    target_id = source_id
                    if kind == "copy":
                        target_id = next_mapping_id
                        next_mapping_id += 1
                        absent_keys[target_id] = set(absent_keys[source_id])
                    for target in targets:
                        if isinstance(target, ast.Name):
                            mapping_ids[target.id] = target_id
                            uncertain_mapping_names.discard(target.id)
                elif relationship:
                    for target in targets:
                        if isinstance(target, ast.Name):
                            uncertain_mapping_names.add(target.id)
                elif is_unconditional(node):
                    # A definite rebind to a non-derived object ends mapping taint.
                    for target in targets:
                        if isinstance(target, ast.Name):
                            mapping_ids.pop(target.id, None)
                            uncertain_mapping_names.discard(target.id)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id in mapping_ids:
                key = constant_key(node.args[0]) if node.args else f"<dynamic-expr:{getattr(node, 'lineno', -1)}:{getattr(node, 'col_offset', -1)}>"
                if node.func.attr == "get":
                    control_keys.add(key)
                if node.func.attr == "pop":
                    control_keys.add(key)
                    if is_unconditional(node):
                        absent_keys[mapping_ids[node.func.value.id]].add(key)
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id in mapping_ids:
                key = constant_key(node.slice)
                if isinstance(node.ctx, ast.Load):
                    control_keys.add(key)
                if isinstance(node.ctx, ast.Del):
                    control_keys.add(key)
                    if is_unconditional(node):
                        absent_keys[mapping_ids[node.value.id]].add(key)
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg is None:
                        relationship = derived_mapping(keyword.value)
                        if relationship and relationship[0] == "conditional" and control_keys:
                            leaked = sorted(control_keys)
                        elif relationship and relationship[1] is not None:
                            forwarded_id = relationship[1]
                            leaked = sorted(control_keys - absent_keys[forwarded_id])
                        else:
                            leaked = []
                        if leaked:
                            issue_key = "dynamic" if is_dynamic_key(leaked[0]) else leaked[0]
                            return PythonBlockCheck(True, False, f"control keyword forwarded to wrapped function: {issue_key}")
    return PythonBlockCheck(True, True)


class GoldenFixtureSandboxedCodeValidator(SandboxedCodeValidator):
    """Runs only audited, hash-pinned Golden fixtures in an ephemeral subprocess."""

    fixtures = {
        "GOLDEN-CODE-002": (
            hashlib.sha256(GOLDEN_CODE_002_SOURCE.encode("utf-8")).hexdigest(),
            "GOLDEN_CODE_002_PASS",
        )
    }

    def validate_python(self, source: str, fixture_id: str | None = None) -> CodeValidationResult:
        static = check_python_block(source)
        if not static.syntax_valid or not static.standalone_claim_ok:
            return CodeValidationResult(CodeValidationLevel.UNVALIDATED, static.issue)
        fixture = self.fixtures.get(fixture_id or "")
        source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
        if not fixture or source_hash != fixture[0]:
            return CodeValidationResult(CodeValidationLevel.STATIC_VALIDATED, "execution denied for non-Golden source")
        safety_issue = self._fixture_safety_issue(source)
        if safety_issue:
            return CodeValidationResult(CodeValidationLevel.STATIC_VALIDATED, safety_issue)
        with tempfile.TemporaryDirectory(prefix="local-ai-golden-code-") as directory:
            try:
                process = subprocess.Popen(
                    [sys.executable, "-I", "-c", source],
                    cwd=directory,
                    env={"PATH": os.defpath, "PYTHONHASHSEED": "0"},
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    shell=False,
                    preexec_fn=self._apply_limits,
                )
                deadline, peak_rss_kib, issue = time.monotonic() + 2, 0, None
                while process.poll() is None:
                    if time.monotonic() >= deadline:
                        issue = "sandbox timeout"
                        process.kill()
                        break
                    rss = subprocess.run(
                        ["/bin/ps", "-o", "rss=", "-p", str(process.pid)],
                        capture_output=True,
                        text=True,
                        timeout=0.2,
                        shell=False,
                        check=False,
                    ).stdout.strip()
                    if rss.isdigit():
                        peak_rss_kib = max(peak_rss_kib, int(rss))
                    if peak_rss_kib > 256 * 1024:
                        issue = "sandbox memory limit exceeded"
                        process.kill()
                        break
                    time.sleep(0.01)
                stdout, _stderr = process.communicate(timeout=0.5)
            except (OSError, subprocess.SubprocessError) as error:
                return CodeValidationResult(CodeValidationLevel.STATIC_VALIDATED, f"sandbox execution failed: {type(error).__name__}")
        if issue:
            return CodeValidationResult(CodeValidationLevel.STATIC_VALIDATED, issue, False, peak_rss_kib)
        marker_seen = fixture[1] in stdout
        if process.returncode != 0 or not marker_seen:
            return CodeValidationResult(CodeValidationLevel.STATIC_VALIDATED, "Golden execution assertions failed", marker_seen, peak_rss_kib)
        return CodeValidationResult(CodeValidationLevel.SANDBOX_EXECUTION_VALIDATED, None, True, peak_rss_kib)

    @staticmethod
    def _fixture_safety_issue(source):
        tree = ast.parse(source)
        allowed_imports = {"functools"}
        denied_calls = {"open", "exec", "eval", "compile", "__import__", "input"}
        denied_roots = {"socket", "urllib", "requests", "http", "subprocess", "pathlib", "os", "shutil"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(alias.name.split(".")[0] not in allowed_imports for alias in node.names):
                return "Golden fixture import denied"
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] not in allowed_imports:
                return "Golden fixture import denied"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in denied_calls:
                return "Golden fixture call denied"
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in denied_roots:
                return "Golden fixture capability denied"
        return None

    @staticmethod
    def _apply_limits():
        resource.setrlimit(resource.RLIMIT_CPU, (1, 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (16, 16))
        resource.setrlimit(resource.RLIMIT_NPROC, (1, 1))
