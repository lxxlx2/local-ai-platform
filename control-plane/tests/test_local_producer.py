import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_ai_control.services.local_producer import (
    LocalPatchProducer, LocalProducerError, _bounded_file_excerpt, _parse_model_json,
    check_patch, discover_context_paths, require_safe_worktree, validate_patch,
)
from local_ai_control.services.omlx import ModelReply


def init_repo(tmp_path):
    root=tmp_path
    (root/"control-plane/src").mkdir(parents=True)
    (root/"control-plane/tests").mkdir(parents=True)
    (root/"docs").mkdir(parents=True)
    import subprocess
    subprocess.run(["git","init","-b","feat/test"],cwd=root,check=True,capture_output=True)
    subprocess.run(["git","config","user.email","test@example.com"],cwd=root,check=True)
    subprocess.run(["git","config","user.name","test"],cwd=root,check=True)
    target=root/"control-plane/src/example.py"; target.write_text("VALUE = 1\n")
    subprocess.run(["git","add","."],cwd=root,check=True)
    subprocess.run(["git","commit","-m","base"],cwd=root,check=True,capture_output=True)
    return root


def test_json_parser_is_strict_but_accepts_single_json_fence():
    payload=_parse_model_json('```json\n{"summary":"ok","patch":"diff"}\n```')
    assert payload=={"summary":"ok","patch":"diff"}
    with pytest.raises(LocalProducerError): _parse_model_json("text before {}")
    with pytest.raises(LocalProducerError): _parse_model_json('{"summary":"ok","patch":"x","extra":1}')


def test_excerpt_prefers_relevant_windows_and_is_bounded():
    text="\n".join(f"line {i}" for i in range(300))+"\nclass HeavyModelConflict: pass\n"+"x"*20000
    excerpt,truncated=_bounded_file_excerpt(text,"fix HeavyModelConflict process termination",2000)
    assert truncated and "HeavyModelConflict" in excerpt and len(excerpt.encode())<=2200


def test_patch_policy_accepts_tracked_edit_and_rejects_escape_delete_and_rename(tmp_path):
    root=init_repo(tmp_path)
    patch="""diff --git a/control-plane/src/example.py b/control-plane/src/example.py
--- a/control-plane/src/example.py
+++ b/control-plane/src/example.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = 2
"""
    assert validate_patch(patch,root)==("control-plane/src/example.py",)
    check_patch(patch,root)
    with pytest.raises(LocalProducerError): validate_patch(patch.replace("control-plane/src/example.py","../escape.py"),root)
    with pytest.raises(LocalProducerError): validate_patch("deleted file mode 100644\n"+patch,root)
    with pytest.raises(LocalProducerError): validate_patch(patch.replace("b/control-plane/src/example.py","b/control-plane/src/other.py",1),root)


def test_patch_policy_allows_safe_new_text_file_but_denies_runtime(tmp_path):
    root=init_repo(tmp_path)
    patch="""diff --git a/control-plane/tests/test_new.py b/control-plane/tests/test_new.py
new file mode 100644
--- /dev/null
+++ b/control-plane/tests/test_new.py
@@ -0,0 +1 @@
+def test_new(): assert True
"""
    assert validate_patch(patch,root)==("control-plane/tests/test_new.py",)
    bad=patch.replace("control-plane/tests/test_new.py","runtime/secret.py")
    with pytest.raises(LocalProducerError): validate_patch(bad,root)


def test_producer_repairs_once_after_invalid_patch(tmp_path):
    root=init_repo(tmp_path)
    good_patch="""diff --git a/control-plane/src/example.py b/control-plane/src/example.py
--- a/control-plane/src/example.py
+++ b/control-plane/src/example.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = 2
"""
    responses=iter((
        ModelReply("not json","completed",None,1,4096),
        ModelReply(json.dumps({"summary":"fix","patch":good_patch}),"completed",None,1,4096),
    ))
    provider=SimpleNamespace(generate=lambda *_a,**_k: next(responses))
    producer=LocalPatchProducer(provider,repo_root=root)
    proposal=producer.propose("change VALUE",["control-plane/src/example.py"],attempts=2)
    assert proposal.paths==("control-plane/src/example.py",) and proposal.summary=="fix"


def test_clean_feature_branch_required(tmp_path):
    root=init_repo(tmp_path)
    assert require_safe_worktree(root)=="feat/test"
    (root/"control-plane/src/example.py").write_text("dirty\n")
    with pytest.raises(LocalProducerError): require_safe_worktree(root)


def test_auto_context_discovers_runtime_files_from_task(tmp_path):
    root=tmp_path
    for rel in (
        "control-plane/src/local_ai_control/services/runtime_providers.py",
        "control-plane/src/local_ai_control/services/qwen38_runtime.py",
        "control-plane/tests/test_runtime_async_r3.py",
        "control-plane/src/local_ai_control/supervisor/process_identity.py",
    ):
        path=root/rel; path.parent.mkdir(parents=True,exist_ok=True); path.write_text("safe\n")
    paths=discover_context_paths("fix Qwen3.8 heavy process failover",root)
    assert "control-plane/src/local_ai_control/services/runtime_providers.py" in paths
    assert "control-plane/src/local_ai_control/supervisor/process_identity.py" in paths
