import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_ai_control.services.local_producer import (
    LocalPatchProducer, LocalProducerError, _bounded_file_excerpt, _parse_model_json,
    check_patch, discover_context_paths, materialize_edits, require_safe_worktree, validate_patch,
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
    target=root/"control-plane/src/example.py"; target.write_text("VALUE = 1\nOTHER = 1\n")
    subprocess.run(["git","add","."],cwd=root,check=True)
    subprocess.run(["git","commit","-m","base"],cwd=root,check=True,capture_output=True)
    return root


def edit_patch():
    return """diff --git a/control-plane/src/example.py b/control-plane/src/example.py
--- a/control-plane/src/example.py
+++ b/control-plane/src/example.py
@@ -1,2 +1,2 @@
-VALUE = 1
+VALUE = 2
 OTHER = 1
"""


def edit_payload(old="VALUE = 1", new="VALUE = 2"):
    return {"summary":"fix","edits":[{"path":"control-plane/src/example.py","old":old,"new":new}]}


def test_json_parser_is_strict_but_accepts_single_json_fence():
    payload=_parse_model_json('```json\n'+json.dumps(edit_payload())+'\n```')
    assert payload==edit_payload()
    with pytest.raises(LocalProducerError): _parse_model_json("text before {}")
    with pytest.raises(LocalProducerError): _parse_model_json(json.dumps({**edit_payload(),"extra":1}))
    with pytest.raises(LocalProducerError): _parse_model_json(json.dumps({"summary":"x","edits":[]}))


def test_excerpt_prefers_relevant_windows_and_is_bounded():
    text="\n".join(f"line {i}" for i in range(300))+"\nclass HeavyModelConflict: pass\n"+"x"*20000
    excerpt,truncated=_bounded_file_excerpt(text,"fix HeavyModelConflict process termination",2000)
    assert truncated and "HeavyModelConflict" in excerpt and len(excerpt.encode())<=2200


def test_patch_policy_accepts_tracked_edit_and_rejects_escape_delete_rename_and_metadata_forgery(tmp_path):
    root=init_repo(tmp_path); patch=edit_patch()
    assert validate_patch(patch,root)==("control-plane/src/example.py",)
    check_patch(patch,root)
    with pytest.raises(LocalProducerError): validate_patch(patch.replace("control-plane/src/example.py","../escape.py"),root)
    with pytest.raises(LocalProducerError): validate_patch("deleted file mode 100644\n"+patch,root)
    with pytest.raises(LocalProducerError): validate_patch(patch.replace("b/control-plane/src/example.py","b/control-plane/src/other.py",1),root)
    forged=patch.replace("+++ b/control-plane/src/example.py","+++ b/control-plane/tests/escape.py")
    with pytest.raises(LocalProducerError,match="metadata path mismatch"): validate_patch(forged,root)
    with pytest.raises(LocalProducerError): validate_patch("noise\n"+patch,root)


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


def test_structured_edit_materializes_host_generated_patch_and_binds_context_snapshot(tmp_path):
    from local_ai_control.services.local_producer import build_context
    root=init_repo(tmp_path)
    context=build_context("change VALUE",["control-plane/src/example.py"],root)
    patch=materialize_edits(edit_payload()["edits"],context,root)
    assert "-VALUE = 1" in patch and "+VALUE = 2" in patch
    check_patch(patch,root)
    (root/"control-plane/src/example.py").write_text("VALUE = 9\nOTHER = 1\n")
    with pytest.raises(LocalProducerError,match="snapshot changed"):
        materialize_edits(edit_payload()["edits"],context,root)


def test_structured_edit_requires_path_in_context_and_unique_old_block(tmp_path):
    from local_ai_control.services.local_producer import build_context
    root=init_repo(tmp_path)
    context=build_context("change VALUE",["control-plane/src/example.py"],root)
    with pytest.raises(LocalProducerError,match="not supplied as safe context"):
        materialize_edits([{"path":"control-plane/tests/x.py","old":"x","new":"y"}],context,root)
    ambiguous=[{"path":"control-plane/src/example.py","old":"1","new":"2"}]
    with pytest.raises(LocalProducerError,match="matched 2 times"):
        materialize_edits(ambiguous,context,root)


def test_producer_repairs_once_after_invalid_structured_edit(tmp_path):
    root=init_repo(tmp_path)
    responses=iter((
        ModelReply(json.dumps(edit_payload("MISSING = 1","VALUE = 2")),"completed",None,1,4096),
        ModelReply(json.dumps(edit_payload()),"completed",None,1,4096),
    ))
    provider=SimpleNamespace(generate=lambda *_a,**_k: next(responses))
    producer=LocalPatchProducer(provider,repo_root=root)
    proposal=producer.propose("change VALUE",["control-plane/src/example.py"],attempts=2)
    assert proposal.paths==("control-plane/src/example.py",) and proposal.summary=="fix"
    assert "-VALUE = 1" in proposal.patch and "+VALUE = 2" in proposal.patch


def test_clean_feature_branch_required(tmp_path):
    root=init_repo(tmp_path)
    assert require_safe_worktree(root)=="feat/test"
    (root/"control-plane/src/example.py").write_text("dirty\n")
    with pytest.raises(LocalProducerError): require_safe_worktree(root)


def test_auto_context_discovers_runtime_files_from_task(tmp_path):
    root=tmp_path
    (root/"docs").mkdir(parents=True)
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
