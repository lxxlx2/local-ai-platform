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


def test_cleanup_removes_published_local_video_and_bounded_presentation_artifacts(tmp_path):
    workspace,candidate=workspace_at_review(tmp_path)
    MediaApprovalService().approve(
        workspace,
        owner_id="owner",
        **candidate,
    )

    repo,bare=temp_public_repo(tmp_path)

    MediaPublisher(
        repo,
        expected_remote=str(bare),
    ).publish(workspace)

    presentation_root=tmp_path/"presentation-jobs"
    presentation=presentation_root/"presentation-test"

    (presentation/"audio").mkdir(parents=True)
    (presentation/"slides").mkdir()
    (presentation/"segments").mkdir()
    (presentation/"output").mkdir()

    (presentation/"audio"/"slide.wav").write_bytes(b"audio")
    (presentation/"slides"/"slide.png").write_bytes(b"png")
    (presentation/"segments"/"slide.mp4").write_bytes(b"segment")
    (presentation/"output"/"presentation.mp4").write_bytes(b"video")
    (presentation/"manifest.json").write_text("retain")
    older=presentation_root/"presentation-old"
    (older/"audio").mkdir(parents=True)
    (older/"audio"/"slide.wav").write_bytes(b"old-audio")
    (older/"manifest.json").write_text("retain")

    result=MediaCleanup(
        allowed_derived_roots=(presentation_root,)
    ).cleanup(
        workspace,
        derived_workspaces=(older,presentation),
    )

    assert workspace.load().state is MediaWorkflowState.ARCHIVED

    assert not (workspace.path/"output"/"presentation.mp4").exists()
    assert not (presentation/"audio"/"slide.wav").exists()
    assert not (presentation/"slides"/"slide.png").exists()
    assert not (presentation/"segments"/"slide.mp4").exists()
    assert not (presentation/"output"/"presentation.mp4").exists()
    assert not (older/"audio"/"slide.wav").exists()

    assert (presentation/"manifest.json").is_file()
    assert (older/"manifest.json").is_file()

    assert any(
        item == "output/presentation.mp4"
        for item in result["removed"]
    )


def test_cleanup_rejects_derived_workspace_outside_allowlist(tmp_path):
    workspace,candidate=workspace_at_review(tmp_path)

    MediaApprovalService().approve(
        workspace,
        owner_id="owner",
        **candidate,
    )

    repo,bare=temp_public_repo(tmp_path)

    MediaPublisher(
        repo,
        expected_remote=str(bare),
    ).publish(workspace)

    outside=tmp_path/"outside"
    outside.mkdir()

    with pytest.raises(MediaWorkflowError,match="DERIVED_ROOT_DENIED"):
        MediaCleanup(
            allowed_derived_roots=(tmp_path/"allowed",)
        ).cleanup(
            workspace,
            derived_workspaces=(outside,),
        )


def test_cleanup_preflights_symlinks_before_transition_or_deletion(tmp_path):
    workspace,candidate=workspace_at_review(tmp_path)
    MediaApprovalService().approve(workspace,owner_id="owner",**candidate)
    repo,bare=temp_public_repo(tmp_path)
    MediaPublisher(repo,expected_remote=str(bare)).publish(workspace)
    outside=tmp_path/"outside.wav"; outside.write_bytes(b"private")
    (workspace.path/"audio"/"unsafe.wav").symlink_to(outside)
    with pytest.raises(MediaWorkflowError,match="SYMLINK_DENIED"):
        MediaCleanup().cleanup(workspace)
    assert workspace.load().state is MediaWorkflowState.PUBLISHED
    assert (workspace.path/"output"/"presentation.mp4").is_file()
    assert outside.read_bytes()==b"private"


def test_publisher_can_update_existing_task_slug(tmp_path):
    repo,bare=temp_public_repo(tmp_path)

    first,candidate=workspace_at_review(tmp_path/"first")
    MediaApprovalService().approve(
        first,
        owner_id="owner",
        **candidate,
    )

    one=MediaPublisher(
        repo,
        expected_remote=str(bare),
    ).publish(first)

    first_commit=one["commit"]

    second,candidate=workspace_at_review(tmp_path/"second")
    MediaApprovalService().approve(
        second,
        owner_id="owner",
        **candidate,
    )

    two=MediaPublisher(
        repo,
        expected_remote=str(bare),
    ).publish(second)

    assert two["commit"] != first_commit
    assert (
        repo/"launch-video"/"output"/"final.mp4"
    ).is_file()


def test_publish_excludes_private_intake_artifacts(tmp_path):
    workspace,candidate=workspace_at_review(tmp_path)
    workspace.write_artifact("production_brief.md","private owner brief")
    workspace.write_artifact("source_evidence.json",{"source":"private"})
    workspace.write_artifact("scene_plan.json",{"schema_version":"0.2","scenes":[]})
    MediaApprovalService().approve(workspace,owner_id="owner",**candidate)
    repo,bare=temp_public_repo(tmp_path)
    (repo/"launch-video"/"source").mkdir(parents=True)
    (repo/"launch-video"/"source"/"requirements.md").write_text("legacy private intake")
    git("add",".",cwd=repo); git("commit","-m","legacy product",cwd=repo); git("push","origin","HEAD",cwd=repo)
    MediaPublisher(repo,expected_remote=str(bare)).publish(workspace)
    target=repo/"launch-video"
    assert not (target/"source"/"requirements.md").exists()
    assert not (target/"source"/"production_brief.md").exists()
    assert not (target/"source"/"source_evidence.json").exists()
    assert (target/"generated"/"script.txt").is_file()


def test_push_failure_resumes_same_commit_without_duplicate_commit(tmp_path):
    workspace,candidate=workspace_at_review(tmp_path)
    MediaApprovalService().approve(workspace,owner_id="owner",**candidate)
    repo,bare=temp_public_repo(tmp_path)
    calls={"push":0}

    def fail_first_push(argv,**kwargs):
        if "push" in argv:
            calls["push"]+=1
            if calls["push"]==1:
                return subprocess.CompletedProcess(argv,1,"","offline")
        return subprocess.run(argv,**kwargs)

    publisher=MediaPublisher(repo,expected_remote=str(bare),run=fail_first_push)
    with pytest.raises(MediaWorkflowError,match="GIT_FAILED:push"):
        publisher.publish(workspace)
    assert workspace.load().state is MediaWorkflowState.PUBLISH_PENDING
    pending_commit=git("rev-parse","HEAD",cwd=repo).stdout.strip()
    commit_count=git("rev-list","--count","HEAD",cwd=repo).stdout.strip()

    result=publisher.resume_publish(workspace)

    assert result["commit"]==pending_commit
    assert git("rev-list","--count","HEAD",cwd=repo).stdout.strip()==commit_count
    assert workspace.load().state is MediaWorkflowState.PUBLISHED


def test_public_payload_with_secret_assignment_fails_before_repo_mutation(tmp_path):
    workspace,candidate=workspace_at_review(tmp_path)
    workspace.write_artifact("script.txt","API_KEY=not-for-public")
    MediaApprovalService().approve(workspace,owner_id="owner",**candidate)
    repo,bare=temp_public_repo(tmp_path)
    with pytest.raises(MediaWorkflowError,match="PUBLIC_PAYLOAD_SENSITIVE"):
        MediaPublisher(repo,expected_remote=str(bare)).publish(workspace)
    assert not git("status","--porcelain",cwd=repo).stdout.strip()
    assert workspace.load().state is MediaWorkflowState.APPROVED


def test_local_commit_without_push_is_not_claimed_published(tmp_path):
    workspace,candidate=workspace_at_review(tmp_path)
    MediaApprovalService().approve(workspace,owner_id="owner",**candidate)
    repo,bare=temp_public_repo(tmp_path)
    result=MediaPublisher(repo,expected_remote=str(bare)).publish(workspace,push=False)
    assert result["verified"] is False
    assert workspace.load().state is MediaWorkflowState.PUBLISH_PENDING
