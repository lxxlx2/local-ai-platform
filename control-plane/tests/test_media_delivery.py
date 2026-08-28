import json
from pathlib import Path
import subprocess

import pytest

from local_ai_control.services.media_delivery import MediaApprovalService,MediaCleanup,MediaPublisher
from local_ai_control.services.media_workflow import MediaWorkflowError,MediaWorkflowState,new_media_workspace


def git(*args,cwd): return subprocess.run(["git",*args],cwd=cwd,check=True,capture_output=True,text=True)


def workspace_at_review(tmp_path):
    workspace=new_media_workspace("Launch Video","owner",root=tmp_path/"jobs")
    state=MediaWorkflowState.RECEIVED
    path=[MediaWorkflowState.REQUIREMENTS_PENDING,MediaWorkflowState.REQUIREMENTS_READY,MediaWorkflowState.SCRIPT_PENDING,MediaWorkflowState.SCRIPT_READY,MediaWorkflowState.PROFILE_SELECTED,MediaWorkflowState.AUDIO_READY,MediaWorkflowState.VISUAL_READY,MediaWorkflowState.VIDEO_READY]
    for target in path: workspace.transition(target,reason="test")
    workspace.write_artifact("requirements.md","requirements")
    workspace.write_artifact("script.txt","script")
    video=workspace.path/"output"/"presentation.mp4"; video.write_bytes(b"video")
    candidate=MediaApprovalService().submit_for_review(workspace,video,duration_seconds=12.5)
    return workspace,candidate


def temp_public_repo(tmp_path):
    bare=tmp_path/"remote.git"; git("init","--bare",str(bare),cwd=tmp_path)
    repo=tmp_path/"public"; git("init",str(repo),cwd=tmp_path)
    git("config","user.email","test@example.invalid",cwd=repo); git("config","user.name","Test",cwd=repo)
    git("remote","add","origin",str(bare),cwd=repo)
    (repo/".gitattributes").write_text("*.mp4 filter=lfs diff=lfs merge=lfs -text\n")
    (repo/"README.md").write_text("# Products\n"); git("add",".",cwd=repo); git("commit","-m","init",cwd=repo); git("push","-u","origin","HEAD",cwd=repo)
    return repo,bare


def test_approval_binds_exact_hash_and_revision_and_stales_on_regeneration(tmp_path):
    workspace,candidate=workspace_at_review(tmp_path)
    with pytest.raises(MediaWorkflowError,match="STALE"):
        MediaApprovalService().approve(workspace,owner_id="owner",output_sha256="bad",candidate_revision=1)
    binding=MediaApprovalService().approve(workspace,owner_id="owner",**candidate)
    assert binding.output_sha256==candidate["output_sha256"]
    changed=workspace.invalidate_candidate("regenerate")
    assert changed.approval is None and changed.candidate_revision==2


def test_temp_git_publish_verifies_exact_candidate_and_cleanup_is_bounded(tmp_path):
    workspace,candidate=workspace_at_review(tmp_path); MediaApprovalService().approve(workspace,owner_id="owner",**candidate)
    repo,bare=temp_public_repo(tmp_path)
    result=MediaPublisher(repo,expected_remote=str(bare)).publish(workspace)
    assert len(result["commit"])==40 and (repo/"launch-video"/"output"/"final.mp4").read_bytes()==b"video"
    (workspace.path/"audio"/"scene.wav").write_bytes(b"audio")
    (workspace.path/"source"/"owner.txt").write_text("retain")
    cleaned=MediaCleanup().cleanup(workspace)
    assert workspace.load().state is MediaWorkflowState.ARCHIVED
    assert not (workspace.path/"audio"/"scene.wav").exists() and (workspace.path/"source"/"owner.txt").exists()
    assert "job.json" in cleaned["retained"]


def test_publisher_requires_approval_fixed_remote_clean_repo_and_lfs(tmp_path):
    workspace,candidate=workspace_at_review(tmp_path); repo,bare=temp_public_repo(tmp_path)
    with pytest.raises(MediaWorkflowError,match="APPROVAL_REQUIRED"): MediaPublisher(repo,expected_remote=str(bare)).publish(workspace)
    MediaApprovalService().approve(workspace,owner_id="owner",**candidate)
    with pytest.raises(MediaWorkflowError,match="REMOTE_MISMATCH"): MediaPublisher(repo,expected_remote="wrong").publish(workspace)


def test_metadata_rejects_sensitive_or_local_fields():
    with pytest.raises(MediaWorkflowError): MediaPublisher._validate_metadata({"token":"x"})
    with pytest.raises(MediaWorkflowError): MediaPublisher._validate_metadata({"job_id":"/Users/private"})
