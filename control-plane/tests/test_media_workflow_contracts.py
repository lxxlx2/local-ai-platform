import json
from pathlib import Path

import pytest

from local_ai_control.services.media_workflow import (
    CompletionMode, EvidenceIntake, IntakeMode, MediaWorkflowError,
    MediaWorkflowState, MediaWorkspace, Provenance, Requirements, RequirementsStore,
    new_media_workspace,
)
from local_ai_control.services.web_research import HttpResponse


def test_durable_state_machine_restart_and_hashes(tmp_path):
    workspace = new_media_workspace("Launch Video", "owner", root=tmp_path)
    assert workspace.load().state is MediaWorkflowState.RECEIVED
    workspace.transition(MediaWorkflowState.REQUIREMENTS_PENDING, reason="direct brief")
    artifact = workspace.write_artifact("generated/script.txt", "hello")
    reopened = MediaWorkspace(workspace.path.name, tmp_path)
    assert reopened.load().content_hashes["generated/script.txt"] == artifact["sha256"]
    with pytest.raises(MediaWorkflowError, match="TRANSITION_INVALID"):
        reopened.transition(MediaWorkflowState.PUBLISHED, reason="skip")


def test_missing_owner_fact_is_explicit_and_resumable(tmp_path):
    workspace = new_media_workspace("Unknown deadline", "owner", root=tmp_path)
    workspace.transition(MediaWorkflowState.REQUIREMENTS_PENDING, reason="intake")
    job = workspace.transition(MediaWorkflowState.MISSING_OWNER_FACT, reason="What is the deadline?")
    job.missing_owner_fact = "What is the deadline?"; workspace.save(job)
    assert workspace.load().missing_owner_fact == "What is the deadline?"
    assert workspace.transition(MediaWorkflowState.REQUIREMENTS_PENDING, reason="owner answered").state is MediaWorkflowState.REQUIREMENTS_PENDING


def test_bounded_upload_denies_symlink_and_escape(tmp_path):
    workspace = new_media_workspace("Uploads", "owner", intake_mode=IntakeMode.UPLOADS, root=tmp_path/"jobs")
    source = tmp_path/"brief.txt"; source.write_text("safe")
    assert workspace.stage_upload(source)["path"].startswith("source/")
    link = tmp_path/"link.txt"; link.symlink_to(source)
    with pytest.raises(MediaWorkflowError, match="UPLOAD_DENIED"): workspace.stage_upload(link)
    with pytest.raises(MediaWorkflowError, match="PATH_ESCAPE"): workspace.write_artifact("../escape", "x")


def test_url_intake_records_untrusted_provenance_without_live_web(tmp_path):
    body=b"Public contest rules"
    class Fetcher:
        def fetch(self, url): return HttpResponse(200,{"content-type":"text/plain"},body,"https://example.test/rules")
    workspace = new_media_workspace("Contest", "owner", intake_mode=IntakeMode.LINKS, root=tmp_path)
    result = EvidenceIntake(Fetcher()).from_url(workspace,"https://example.test/rules")
    assert result["provenance"]["trust_label"] == "UNTRUSTED_EXTERNAL_CONTENT"
    assert result["provenance"]["content_sha256"] == workspace.load().content_hashes[result["artifact"]["path"]]


def test_requirements_artifacts_are_authoritative_and_structured(tmp_path):
    workspace = new_media_workspace("Contest", "owner", root=tmp_path)
    provenance = Provenance("owner", "now", "a"*64, "brief", "authoritative", "OWNER_PROVIDED")
    requirements = Requirements("Create one English launch video", language_requirements="en", required_questions=("What changed?",), provenance=(provenance,))
    RequirementsStore().persist(workspace, requirements, [{"source":"owner"}])
    value=json.loads(workspace.read_artifact("requirements.json"))
    assert value["objective"].startswith("Create") and value["required_questions"] == ["What changed?"]
    assert set(RequirementsStore.REQUIRED) <= set(workspace.load().content_hashes)


def test_candidate_invalidation_stales_approval(tmp_path):
    workspace = new_media_workspace("Review", "owner", completion_mode=CompletionMode.SCRIPT_REVIEW_FIRST, root=tmp_path)
    job=workspace.load(); job.approval={"sha256":"x"}; workspace.save(job)
    changed=workspace.invalidate_candidate("script edit")
    assert changed.candidate_revision == 2 and changed.approval is None
