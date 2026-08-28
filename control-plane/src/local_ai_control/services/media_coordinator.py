"""Owner-facing orchestration for Media Product Workflow V0.2."""
from __future__ import annotations

import json
from pathlib import Path

from .media_delivery import (
    MediaApprovalService,
    MediaCleanup,
    MediaPublisher,
)
from .media_execution import (
    MediaExecutionResult,
    MediaScriptReviewResult,
    MediaExecutionService,
)
from .media_production import ScriptParser
from .presentation_jobs import PPTXParser
from .media_workflow import (
    DEFAULT_ROOT,
    MediaWorkflowError,
    MediaWorkflowState,
    MediaWorkspace,
    sha256_file,
)


PRESENTATION_ROOT = Path("/Users/jerson/AI/runtime/presentation-jobs")


class MediaProductCoordinator:
    def __init__(
        self,
        *,
        job_root=DEFAULT_ROOT,
        presentation_root=PRESENTATION_ROOT,
        publisher=None,
        execution_factory=MediaExecutionService,
    ):
        self.job_root=Path(job_root)
        self.presentation_root=Path(presentation_root)
        self.publisher=publisher or MediaPublisher()
        self.execution_factory=execution_factory

    def _workspace(self, owner_id: str, job_ref: str) -> MediaWorkspace:
        workspace=MediaWorkspace(job_ref,self.job_root)
        job=workspace.load()
        if job.owner_id != owner_id:
            raise MediaWorkflowError("MEDIA_OWNER_MISMATCH")
        return workspace

    def generate(self, owner_id: str, job_ref: str) -> MediaExecutionResult | MediaScriptReviewResult:
        workspace=self._workspace(owner_id,job_ref)
        job=workspace.load()

        if job.state is MediaWorkflowState.REVIEW_PENDING:
            candidate=json.loads(
                workspace.read_artifact("metadata/candidate.json")
            )
            execution=json.loads(
                workspace.read_artifact("metadata/execution.json")
            )
            output=workspace.path/candidate["output_path"]

            if not output.is_file() or sha256_file(output) != candidate["output_sha256"]:
                raise MediaWorkflowError("MEDIA_REVIEW_OUTPUT_CHANGED")

            return MediaExecutionResult(
                job_id=job.job_id,
                state=job.state.value,
                output_path=str(output),
                output_sha256=candidate["output_sha256"],
                duration_seconds=candidate["duration_seconds"],
                candidate_revision=candidate["candidate_revision"],
                presentation_job_path=execution["presentation_job_path"],
            )

        if job.state is MediaWorkflowState.SCRIPT_READY:
            return self.execution_factory(workspace).resume_after_script_review()

        if job.state not in {
            MediaWorkflowState.RECEIVED,
            MediaWorkflowState.REQUIREMENTS_PENDING,
        }:
            raise MediaWorkflowError(
                f"MEDIA_GENERATION_RESUME_REQUIRED:{job.state.value}"
            )

        return self.execution_factory(workspace).run_to_review()

    def missing_owner_fact(self,owner_id:str,job_ref:str)->str|None:
        job=self._workspace(owner_id,job_ref).load()
        return job.missing_owner_fact if job.state is MediaWorkflowState.MISSING_OWNER_FACT else None

    def provide_owner_fact(self,owner_id:str,job_ref:str,answer:str)->dict:
        workspace=self._workspace(owner_id,job_ref); job=workspace.load()
        value=answer.strip()
        if job.state is not MediaWorkflowState.MISSING_OWNER_FACT or not value or len(value)>10_000:
            raise MediaWorkflowError("MEDIA_OWNER_FACT_INVALID")
        workspace.write_artifact("source/owner-facts.txt",value+"\n")
        job=workspace.transition(MediaWorkflowState.REQUIREMENTS_PENDING,reason="owner supplied missing fact")
        job.missing_owner_fact=None; workspace.save(job)
        return {"state":job.state.value,"job_id":job.job_id}

    def revise_script(self,owner_id:str,job_ref:str,script_text:str)->MediaScriptReviewResult:
        workspace=self._workspace(owner_id,job_ref); job=workspace.load()
        if job.state not in {MediaWorkflowState.SCRIPT_READY,MediaWorkflowState.REVIEW_PENDING,MediaWorkflowState.APPROVED}:
            raise MediaWorkflowError("MEDIA_SCRIPT_REVISION_STATE_INVALID")
        job=workspace.invalidate_candidate("owner script revision")
        if job.state is MediaWorkflowState.SCRIPT_READY:
            workspace.transition(MediaWorkflowState.SCRIPT_PENDING,reason="owner script revision")
        request=json.loads(workspace.read_artifact("metadata/request.json"))
        document=ScriptParser().parse(script_text,title=job.task_name,language=request["language"])
        deck=workspace.path/"generated"/"presentation.pptx"
        if not deck.is_file():
            for upload in request.get("uploads",[]):
                candidate=workspace._inside(upload["path"])
                if candidate.suffix.lower()==".pptx": deck=candidate; break
        if not deck.is_file() or len(document.scenes)!=len(PPTXParser().parse(deck)):
            raise MediaWorkflowError("SCRIPT_SCENE_COUNT_MISMATCH")
        canonical="\n\n".join(f"## {scene.title}\n{scene.narration}" for scene in document.scenes)+"\n"
        workspace.write_artifact("script.txt",canonical)
        workspace.write_artifact("scene_plan.json",{"schema_version":"0.2","title":document.title,"language":document.language,
                                                      "scenes":[scene.__dict__ for scene in document.scenes]})
        workspace.transition(MediaWorkflowState.SCRIPT_READY,reason="owner revised script ready")
        return MediaScriptReviewResult(job.job_id,MediaWorkflowState.SCRIPT_READY.value,canonical,workspace.load().candidate_revision)

    def regenerate(self,owner_id:str,job_ref:str)->MediaExecutionResult:
        workspace=self._workspace(owner_id,job_ref); job=workspace.load()
        if job.state is not MediaWorkflowState.REVIEW_PENDING:
            raise MediaWorkflowError("MEDIA_REGENERATE_STATE_INVALID")
        workspace.invalidate_candidate("owner requested regeneration")
        workspace.transition(MediaWorkflowState.SCRIPT_READY,reason="reuse approved script for regeneration")
        return self.execution_factory(workspace).resume_after_script_review()

    def approve_publish(
        self,
        owner_id: str,
        job_ref: str,
        *,
        output_sha256: str,
        candidate_revision: int,
        keep_local: bool=False,
    ) -> dict:
        workspace=self._workspace(owner_id,job_ref)
        job=workspace.load()

        if job.state is MediaWorkflowState.REVIEW_PENDING:
            MediaApprovalService().approve(
                workspace,
                owner_id=owner_id,
                output_sha256=output_sha256,
                candidate_revision=candidate_revision,
            )
            job=workspace.load()

        elif job.state in {
            MediaWorkflowState.APPROVED,
            MediaWorkflowState.PUBLISH_PENDING,
        }:
            approval=job.approval or {}
            if (
                approval.get("output_sha256") != output_sha256
                or approval.get("candidate_revision") != candidate_revision
            ):
                raise MediaWorkflowError("MEDIA_APPROVAL_STALE")

        else:
            raise MediaWorkflowError(
                f"MEDIA_APPROVAL_STATE_INVALID:{job.state.value}"
            )

        if job.state is MediaWorkflowState.APPROVED:
            publish=self.publisher.publish(workspace)
        else:
            publish=self.publisher.resume_publish(workspace)

        execution=json.loads(
            workspace.read_artifact("metadata/execution.json")
        )

        presentation_paths=execution.get("presentation_job_paths") or [execution["presentation_job_path"]]

        cleanup=MediaCleanup(
            allowed_derived_roots=(self.presentation_root,)
        ).cleanup(
            workspace,
            keep_local=keep_local,
            derived_workspaces=tuple(Path(item) for item in presentation_paths),
        )

        return {
            "state":workspace.load().state.value,
            "commit":publish["commit"],
            "output_sha256":publish["output_sha256"],
            "cleanup":cleanup,
        }

    def cancel_review(self, owner_id: str, job_ref: str) -> dict:
        workspace=self._workspace(owner_id,job_ref)
        job=workspace.load()

        if job.state not in {
            MediaWorkflowState.MISSING_OWNER_FACT,
            MediaWorkflowState.SCRIPT_READY,
            MediaWorkflowState.REVIEW_PENDING,
        }:
            raise MediaWorkflowError("MEDIA_CANCEL_REVIEW_STATE_INVALID")

        job=workspace.transition(
            MediaWorkflowState.CANCELLED,
            reason="owner cancelled reviewed candidate",
        )

        return {
            "state":job.state.value,
            "job_id":job.job_id,
        }
