from pathlib import Path
import pytest

from local_ai_control.bot.media_wizard import (
    MediaWizardController,
    MediaWizardStore,
)
from local_ai_control.domain.identity import Role
from local_ai_control.services.media_execution import MediaExecutionService,MediaScriptReviewResult
from local_ai_control.services.media_coordinator import MediaProductCoordinator
from local_ai_control.services.media_production import DeterministicDeckBuilder,ScriptDocument,ScriptScene
from local_ai_control.services.media_workflow import MediaWorkflowState,new_media_workspace


def test_confirm_persists_execution_request_and_inputs(tmp_path):
    controller = MediaWizardController(
        MediaWizardStore(tmp_path / "wizard.db"),
        job_root=tmp_path / "jobs",
        staging_root=tmp_path / "staging",
    )

    controller.start(Role.OWNER, "owner")
    controller.text(
        Role.OWNER,
        "owner",
        "Video Demo",
    )

    controller.choice(
        Role.OWNER,
        "owner",
        "source_mode",
        "UPLOADS",
    )

    controller.stage_upload_bytes(
        Role.OWNER,
        "owner",
        filename="script.txt",
        payload=b"## One\nHello",
    )

    controller.finish_materials(
        Role.OWNER,
        "owner",
    )

    controller.choice(
        Role.OWNER,
        "owner",
        "execution_mode",
        "AUTO",
    )

    controller.choice(
        Role.OWNER,
        "owner",
        "language",
        "en",
    )

    controller.choice(
        Role.OWNER,
        "owner",
        "voice",
        "en-male-25-default",
    )

    controller.choice(
        Role.OWNER,
        "owner",
        "completion_mode",
        "AUTO_COMPLETE",
    )

    created = controller.confirm(
        Role.OWNER,
        "owner",
    )

    job_root = (
        tmp_path
        / "jobs"
        / created.values["job_ref"]
    )

    request = (
        job_root
        / "metadata"
        / "request.json"
    ).read_text("utf-8")

    assert '"language": "en"' in request
    assert '"voice": "en-male-25-default"' in request
    assert '"source_mode": "UPLOADS"' in request

    source_files = list(
        (job_root / "source").glob("*.txt")
    )

    assert len(source_files) == 1


class _Reply:
    status="completed"
    incomplete_reason=None
    text="## Opening\nA useful introduction.\n\n## Close\nA concise conclusion."


class _Provider:
    def __init__(self): self.calls=0
    def generate(self,prompt,max_output_tokens):
        self.calls+=1
        return _Reply()


def _request(workspace,**values):
    workspace.write_artifact("metadata/request.json",{
        "schema_version":"0.2","task_name":workspace.load().task_name,
        "source_mode":"DIRECT_BRIEF","execution_mode":"REVIEW_SCRIPT",
        "language":"en","voice":"auto","completion_mode":"SCRIPT_REVIEW_FIRST",
        "uploads":[],"source_urls":[],**values,
    })


def test_script_review_first_stops_before_audio_or_video(tmp_path):
    workspace=new_media_workspace("Review First","owner",root=tmp_path/"jobs")
    workspace.write_artifact("source/direct-brief.txt","Explain a safe local workflow.\n")
    _request(workspace)
    provider=_Provider()
    result=MediaExecutionService(
        workspace,provider=provider,presentation_root=tmp_path/"presentations",
    ).run_to_review()
    assert isinstance(result,MediaScriptReviewResult)
    assert result.state==MediaWorkflowState.SCRIPT_READY.value
    assert provider.calls==1
    assert not (workspace.path/"output"/"final.mp4").exists()
    assert not (tmp_path/"presentations").exists()


def test_supplied_ppt_and_script_reaches_review_without_qwen_rewrite(tmp_path):
    workspace=new_media_workspace("Owner Materials","owner",root=tmp_path/"jobs")
    document=ScriptDocument("Owner","en",(
        ScriptScene(1,"Opening","Owner narration one."),
        ScriptScene(2,"Close","Owner narration two."),
    ))
    deck=DeterministicDeckBuilder().build(document,tmp_path/"deck.pptx")
    script=tmp_path/"script.txt"; script.write_text("## Opening\nOwner narration one.\n\n## Close\nOwner narration two.\n")
    deck_record=workspace.stage_upload(deck); script_record=workspace.stage_upload(script)
    _request(workspace,source_mode="UPLOADS",uploads=[
        {"name":"deck.pptx",**deck_record},{"name":"script.txt",**script_record},
    ])
    provider=_Provider()
    result=MediaExecutionService(
        workspace,provider=provider,presentation_root=tmp_path/"presentations",
    ).run_to_review()
    assert isinstance(result,MediaScriptReviewResult)
    assert provider.calls==0
    assert "Owner narration one" in result.script_text


def test_coordinator_resumes_script_ready_instead_of_restarting(tmp_path):
    workspace=new_media_workspace("Resume","owner",root=tmp_path/"jobs")
    workspace.transition(MediaWorkflowState.REQUIREMENTS_PENDING,reason="test")
    workspace.transition(MediaWorkflowState.REQUIREMENTS_READY,reason="test")
    workspace.transition(MediaWorkflowState.SCRIPT_PENDING,reason="test")
    workspace.transition(MediaWorkflowState.SCRIPT_READY,reason="test")
    calls=[]
    class Execution:
        def __init__(self,value): calls.append(("init",value.path.name))
        def resume_after_script_review(self): calls.append(("resume",)); return "resumed"
    coordinator=MediaProductCoordinator(job_root=tmp_path/"jobs",execution_factory=Execution)
    assert coordinator.generate("owner",workspace.path.name)=="resumed"
    assert calls[-1]==("resume",)


def test_missing_owner_fact_answer_is_durable_and_resumable(tmp_path):
    workspace=new_media_workspace("Missing Fact","owner",root=tmp_path/"jobs")
    workspace.transition(MediaWorkflowState.REQUIREMENTS_PENDING,reason="test")
    job=workspace.transition(MediaWorkflowState.MISSING_OWNER_FACT,reason="Which launch date?")
    job.missing_owner_fact="Which launch date?"; workspace.save(job)
    coordinator=MediaProductCoordinator(job_root=tmp_path/"jobs")
    assert coordinator.missing_owner_fact("owner",workspace.path.name)=="Which launch date?"
    resumed=coordinator.provide_owner_fact("owner",workspace.path.name,"Launch on 3 September.")
    assert resumed["state"]==MediaWorkflowState.REQUIREMENTS_PENDING.value
    assert workspace.read_artifact("source/owner-facts.txt")==b"Launch on 3 September.\n"
    assert workspace.load().missing_owner_fact is None


def test_script_review_can_be_cancelled_without_video(tmp_path):
    workspace=new_media_workspace("Cancel Script","owner",root=tmp_path/"jobs")
    workspace.transition(MediaWorkflowState.REQUIREMENTS_PENDING,reason="test")
    workspace.transition(MediaWorkflowState.REQUIREMENTS_READY,reason="test")
    workspace.transition(MediaWorkflowState.SCRIPT_PENDING,reason="test")
    workspace.transition(MediaWorkflowState.SCRIPT_READY,reason="test")
    coordinator=MediaProductCoordinator(job_root=tmp_path/"jobs")
    assert coordinator.cancel_review("owner",workspace.path.name)["state"]==MediaWorkflowState.CANCELLED.value


@pytest.mark.parametrize(("suffix","category"),[
    (".docx","MEDIA_DOCX_PARSER_NOT_QUALIFIED"),
    (".pdf","MEDIA_PDF_PARSER_NOT_QUALIFIED"),
    (".png","MEDIA_BINARY_INPUT_PARSER_NOT_QUALIFIED"),
])
def test_unqualified_input_parsers_fail_explicitly(tmp_path,suffix,category):
    workspace=new_media_workspace("Unsupported","owner",root=tmp_path/"jobs")
    source=tmp_path/f"material{suffix}"; source.write_bytes(b"not parsed")
    record=workspace.stage_upload(source)
    _request(workspace,source_mode="UPLOADS",uploads=[{"name":source.name,**record}])
    with pytest.raises(RuntimeError,match=category):
        MediaExecutionService(workspace,provider=_Provider(),presentation_root=tmp_path/"presentations").run_to_review()


def test_candidate_revision_uses_new_presentation_workspace(tmp_path):
    workspace=new_media_workspace("Revision","owner",root=tmp_path/"jobs")
    document=ScriptDocument("Revision","en",(ScriptScene(1,"One","Narration."),))
    deck=DeterministicDeckBuilder().build(document,tmp_path/"deck.pptx")
    service=MediaExecutionService(workspace,provider=_Provider(),presentation_root=tmp_path/"presentations")
    first=service._presentation_job(deck)
    workspace.invalidate_candidate("owner revision")
    second=service._presentation_job(deck)
    assert first.path != second.path
    assert first.path.name.endswith("-r1")
    assert second.path.name.endswith("-r2")
