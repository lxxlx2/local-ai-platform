"""Exact-output approval, fixed-target publishing, verification, and bounded cleanup."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from .media_workflow import MediaWorkflowError, MediaWorkflowState, MediaWorkspace, sha256_file, utc_now


CANONICAL_REPOSITORY = Path("/Users/jerson/ai_video_product")
CANONICAL_REMOTE = "https://github.com/lxxlx2/ai_video_product.git"
SAFE_METADATA_KEYS = {"schema_version","job_id","task_slug","candidate_revision","output_sha256","duration_seconds","language","voice_profile_id","voice_profile_revision","published_at","source_evidence_sha256"}
SECRET_PATTERN = re.compile(
    r"(?i)\b(?:api[_-]?key|token|secret|password|credential|authorization|cookie|private[_ -]?key)\b\s*[:=]"
)
LOCAL_PATH_PATTERN = re.compile(r"(?:/Users/|file://|\.\./)")


@dataclass(frozen=True)
class ApprovalBinding:
    job_id: str
    output_sha256: str
    candidate_revision: int
    approved_by: str
    approved_at: str


class MediaApprovalService:
    def submit_for_review(self, workspace: MediaWorkspace, output: Path, *, duration_seconds: float) -> dict:
        output=Path(output)
        if output.is_symlink() or not output.is_file() or output.suffix.lower() != ".mp4" or output.stat().st_size < 1:
            raise MediaWorkflowError("MEDIA_REVIEW_OUTPUT_INVALID")
        expected=(workspace.path/"output").resolve()
        try: output.resolve().relative_to(expected)
        except (OSError,ValueError) as exc: raise MediaWorkflowError("MEDIA_REVIEW_OUTPUT_ESCAPE") from exc
        job=workspace.load()
        if job.state is not MediaWorkflowState.VIDEO_READY: raise MediaWorkflowError("MEDIA_REVIEW_STATE_INVALID")
        digest=sha256_file(output)
        workspace.write_artifact("metadata/candidate.json",{"schema_version":"0.2","candidate_revision":job.candidate_revision,"output_path":str(output.relative_to(workspace.path)),"output_sha256":digest,"duration_seconds":round(duration_seconds,3)})
        workspace.transition(MediaWorkflowState.REVIEW_PENDING,reason="exact video candidate ready")
        return {"output_sha256":digest,"candidate_revision":job.candidate_revision}

    def approve(self, workspace: MediaWorkspace, *, owner_id: str, output_sha256: str, candidate_revision: int) -> ApprovalBinding:
        job=workspace.load()
        if job.owner_id != owner_id or job.state is not MediaWorkflowState.REVIEW_PENDING:
            raise MediaWorkflowError("MEDIA_APPROVAL_DENIED")
        candidate=json.loads(workspace.read_artifact("metadata/candidate.json"))
        if candidate.get("output_sha256") != output_sha256 or candidate.get("candidate_revision") != candidate_revision:
            raise MediaWorkflowError("MEDIA_APPROVAL_STALE")
        binding=ApprovalBinding(job.job_id,output_sha256,candidate_revision,owner_id,utc_now())
        workspace.write_artifact("metadata/approval.json",binding.__dict__)
        job=workspace.transition(MediaWorkflowState.APPROVED,reason="owner exact-output approval")
        job.approval=binding.__dict__; workspace.save(job); return binding


class MediaPublisher:
    """Publishes only to one configured repo; commands are fixed argv and shell-free."""
    def __init__(self, repository: Path | str = CANONICAL_REPOSITORY, *, expected_remote=CANONICAL_REMOTE, run=subprocess.run):
        self.repository=Path(repository).resolve(); self.expected_remote=expected_remote; self.run=run

    def _git(self,*args,check=True):
        result=self.run(["git","-C",str(self.repository),*args],shell=False,capture_output=True,text=True,timeout=120,check=False)
        if check and result.returncode: raise MediaWorkflowError(f"MEDIA_PUBLISH_GIT_FAILED:{args[0]}")
        return result

    def _git_blob(self, revision_path: str) -> bytes:
        result=self.run(
            ["git","-C",str(self.repository),"show",revision_path],
            shell=False,capture_output=True,text=False,timeout=120,check=False,
        )
        if result.returncode:
            raise MediaWorkflowError("MEDIA_PUBLISH_GIT_FAILED:show")
        return result.stdout

    def _preflight(self) -> None:
        if self.repository.is_symlink() or not (self.repository/".git").exists(): raise MediaWorkflowError("MEDIA_PUBLISH_REPOSITORY_INVALID")
        remote=self._git("remote","get-url","origin").stdout.strip()
        if remote != self.expected_remote: raise MediaWorkflowError("MEDIA_PUBLISH_REMOTE_MISMATCH")
        if self._git("status","--porcelain").stdout.strip(): raise MediaWorkflowError("MEDIA_PUBLISH_DIRTY")
        attrs=self.repository/".gitattributes"
        if not attrs.is_file() or "*.mp4 filter=lfs" not in attrs.read_text("utf-8"):
            raise MediaWorkflowError("MEDIA_PUBLISH_LFS_POLICY_MISSING")

    @staticmethod
    def _validate_public_payload(payload: bytes) -> None:
        if len(payload) > 5 * 1024**2 or b"\x00" in payload:
            raise MediaWorkflowError("MEDIA_PUBLISH_PUBLIC_PAYLOAD_INVALID")
        text=payload.decode("utf-8",errors="strict")
        if SECRET_PATTERN.search(text) or LOCAL_PATH_PATTERN.search(text):
            raise MediaWorkflowError("MEDIA_PUBLISH_PUBLIC_PAYLOAD_SENSITIVE")

    def _branch(self) -> str:
        value=self._git("branch","--show-current").stdout.strip()
        if not value:
            raise MediaWorkflowError("MEDIA_PUBLISH_BRANCH_INVALID")
        return value

    def _verify_commit_and_output(self, commit: str, task_slug: str, expected_hash: str) -> None:
        branch=self._branch()
        remote=self._git("ls-remote","origin",f"refs/heads/{branch}").stdout.strip().split()
        if len(remote) != 2 or remote[0] != commit:
            raise MediaWorkflowError("MEDIA_PUBLISH_REMOTE_COMMIT_MISMATCH")
        blob=self._git_blob(f"{commit}:{task_slug}/output/final.mp4")
        pointer=re.search(rb"oid sha256:([0-9a-f]{64})",blob)
        if pointer:
            verified=pointer.group(1).decode()==expected_hash
        else:
            import hashlib
            verified=hashlib.sha256(blob).hexdigest()==expected_hash
        if not verified:
            raise MediaWorkflowError("MEDIA_PUBLISH_REMOTE_OUTPUT_MISMATCH")

    def publish(self, workspace: MediaWorkspace, *, push=True) -> dict:
        job=workspace.load()
        if job.state is not MediaWorkflowState.APPROVED or not job.approval: raise MediaWorkflowError("MEDIA_PUBLISH_APPROVAL_REQUIRED")
        candidate=json.loads(workspace.read_artifact("metadata/candidate.json")); approval=job.approval
        if candidate["output_sha256"] != approval["output_sha256"] or candidate["candidate_revision"] != approval["candidate_revision"]:
            raise MediaWorkflowError("MEDIA_PUBLISH_APPROVAL_STALE")
        source=workspace.path/candidate["output_path"]
        if sha256_file(source) != approval["output_sha256"]: raise MediaWorkflowError("MEDIA_PUBLISH_OUTPUT_CHANGED")
        self._preflight()
        readme=f"# {job.task_name}\n\nLocally generated and Owner-approved media product.\n".encode("utf-8")
        self._validate_public_payload(readme)
        public_payloads={}
        for relative in ("script.txt","scene_plan.json","prompt_pack.json"):
            origin=workspace.path/relative
            if origin.is_file() and not origin.is_symlink():
                payload=origin.read_bytes(); self._validate_public_payload(payload)
                public_payloads[relative]=payload
        target=self.repository/job.task_slug
        if target.exists():
            if target.is_symlink() or not target.is_dir():
                raise MediaWorkflowError("MEDIA_PUBLISH_TARGET_INVALID")
            try:
                target.resolve().relative_to(self.repository)
            except (OSError, ValueError) as exc:
                raise MediaWorkflowError("MEDIA_PUBLISH_TARGET_ESCAPE") from exc
        else:
            target_parent = target.parent.resolve()
            if target_parent != self.repository:
                raise MediaWorkflowError("MEDIA_PUBLISH_TARGET_ESCAPE")

        managed_relatives=(
            "source/requirements.md","source/production_brief.md","source/source_evidence.json",
            "generated/script.txt","generated/scene_plan.json","generated/prompt_pack.json",
        )
        if target.exists():
            for relative in ("source","generated","output","metadata","output/final.mp4","metadata/manifest.json"):
                if (target/relative).is_symlink():
                    raise MediaWorkflowError("MEDIA_PUBLISH_TARGET_SYMLINK_DENIED")
            for relative in managed_relatives:
                if (target/relative).is_symlink():
                    raise MediaWorkflowError("MEDIA_PUBLISH_TARGET_SYMLINK_DENIED")

        workspace.transition(MediaWorkflowState.PUBLISH_PENDING,reason="fixed repository publish")
        target.mkdir(mode=0o755, parents=True, exist_ok=True)

        for relative in ("source","generated","output","metadata"):
            (target/relative).mkdir(mode=0o755,parents=True,exist_ok=True)
        for relative in managed_relatives:
            managed=target/relative
            managed.unlink(missing_ok=True)
        (target/"README.md").write_bytes(readme)
        shutil.copyfile(source,target/"output"/"final.mp4",follow_symlinks=False)
        for relative,payload in public_payloads.items():
            (target/"generated"/relative).write_bytes(payload)
        metadata={"schema_version":"0.2","job_id":job.job_id,"task_slug":job.task_slug,"candidate_revision":job.candidate_revision,"output_sha256":approval["output_sha256"],"duration_seconds":candidate["duration_seconds"],"published_at":utc_now()}
        self._validate_metadata(metadata); (target/"metadata"/"manifest.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+"\n","utf-8")
        self._git("add","--",job.task_slug); self._git("commit","-m",f"publish: {job.task_slug}")
        commit=self._git("rev-parse","HEAD").stdout.strip()
        workspace.write_artifact("metadata/publish_pending.json",{
            "schema_version":"0.2","commit":commit,"task_slug":job.task_slug,
            "output_sha256":approval["output_sha256"],
        })
        if not push:
            return {"state":MediaWorkflowState.PUBLISH_PENDING.value,"commit":commit,
                    "output_sha256":approval["output_sha256"],"verified":False}
        self._git("push","origin",f"HEAD:{self._branch()}")
        self._verify_commit_and_output(commit,job.task_slug,approval["output_sha256"])
        if sha256_file(target/"output"/"final.mp4") != approval["output_sha256"]: raise MediaWorkflowError("MEDIA_PUBLISH_VERIFY_FAILED")
        job=workspace.transition(MediaWorkflowState.PUBLISHED,reason="commit and hash verified")
        job.publish={"repository":self.expected_remote,"commit":commit,"output_sha256":approval["output_sha256"],"verified_at":utc_now()}; workspace.save(job)
        return job.publish

    def resume_publish(self, workspace: MediaWorkspace) -> dict:
        job=workspace.load()
        if job.state is not MediaWorkflowState.PUBLISH_PENDING or not job.approval:
            raise MediaWorkflowError("MEDIA_PUBLISH_RESUME_STATE_INVALID")

        self._preflight()

        target=self.repository/job.task_slug
        output=target/"output"/"final.mp4"

        if target.is_symlink() or not output.is_file() or output.is_symlink():
            raise MediaWorkflowError("MEDIA_PUBLISH_RESUME_OUTPUT_INVALID")

        expected=job.approval["output_sha256"]

        if sha256_file(output) != expected:
            raise MediaWorkflowError("MEDIA_PUBLISH_RESUME_HASH_MISMATCH")

        try:
            pending=json.loads(workspace.read_artifact("metadata/publish_pending.json"))
        except (OSError,ValueError,json.JSONDecodeError) as exc:
            raise MediaWorkflowError("MEDIA_PUBLISH_PENDING_RECORD_INVALID") from exc
        commit=self._git("rev-parse","HEAD").stdout.strip()
        if (
            pending.get("schema_version") != "0.2"
            or pending.get("commit") != commit
            or pending.get("task_slug") != job.task_slug
            or pending.get("output_sha256") != expected
        ):
            raise MediaWorkflowError("MEDIA_PUBLISH_PENDING_RECORD_MISMATCH")
        self._git("push","origin",f"HEAD:{self._branch()}")
        self._verify_commit_and_output(commit,job.task_slug,expected)

        job=workspace.transition(
            MediaWorkflowState.PUBLISHED,
            reason="resumed publish commit and hash verified",
        )
        job.publish={
            "repository":self.expected_remote,
            "commit":commit,
            "output_sha256":expected,
            "verified_at":utc_now(),
        }
        workspace.save(job)
        return job.publish

    @staticmethod
    def _validate_metadata(metadata: dict) -> None:
        if set(metadata)-SAFE_METADATA_KEYS: raise MediaWorkflowError("MEDIA_PUBLISH_METADATA_FIELD_DENIED")
        encoded=json.dumps(metadata,ensure_ascii=False)
        if SECRET_PATTERN.search(encoded) or LOCAL_PATH_PATTERN.search(encoded): raise MediaWorkflowError("MEDIA_PUBLISH_METADATA_SENSITIVE")


class MediaCleanup:
    """Delete only verified, derived media artifacts after successful publish."""

    ELIGIBLE_DIRS = ("audio", "visual", "segments")
    ELIGIBLE_FILES = (
        "generated/presentation.pptx",
        "output/final.mp4",
    )

    PRESENTATION_DIRS = (
        "audio",
        "slides",
        "segments",
    )

    PRESENTATION_FILES = (
        "output/presentation.mp4",
        "source/presentation.pdf",
    )

    def __init__(self, *, allowed_derived_roots=()):
        self.allowed_derived_roots = tuple(
            Path(item).resolve() for item in allowed_derived_roots
        )

    def _validate_derived_workspace(self, path: Path) -> Path:
        path = Path(path)

        if path.is_symlink():
            raise MediaWorkflowError("MEDIA_CLEANUP_SYMLINK_DENIED")

        resolved = path.resolve()

        if not self.allowed_derived_roots:
            raise MediaWorkflowError("MEDIA_CLEANUP_DERIVED_ROOT_DENIED")

        allowed = False
        for root in self.allowed_derived_roots:
            try:
                resolved.relative_to(root)
                allowed = True
                break
            except ValueError:
                continue

        if not allowed:
            raise MediaWorkflowError("MEDIA_CLEANUP_DERIVED_ROOT_DENIED")

        return resolved

    @staticmethod
    def _remove_file(path: Path, removed: list[str], label: str) -> None:
        if path.is_symlink():
            raise MediaWorkflowError("MEDIA_CLEANUP_SYMLINK_DENIED")

        if path.is_file():
            path.unlink()
            removed.append(label)

    @staticmethod
    def _remove_dir_files(
        directory: Path,
        removed: list[str],
        prefix: str,
    ) -> None:
        if directory.is_symlink():
            raise MediaWorkflowError("MEDIA_CLEANUP_SYMLINK_DENIED")

        if not directory.is_dir():
            return

        for item in sorted(directory.rglob("*"), reverse=True):
            if item.is_symlink():
                raise MediaWorkflowError("MEDIA_CLEANUP_SYMLINK_DENIED")

            if item.is_file():
                relative = item.relative_to(directory)
                item.unlink()
                removed.append(f"{prefix}/{relative}")

        for item in sorted(directory.rglob("*"), reverse=True):
            if item.is_dir():
                try:
                    item.rmdir()
                except OSError:
                    pass

    @staticmethod
    def _assert_tree_safe(path: Path) -> None:
        if path.is_symlink():
            raise MediaWorkflowError("MEDIA_CLEANUP_SYMLINK_DENIED")
        if path.is_dir() and any(item.is_symlink() for item in path.rglob("*")):
            raise MediaWorkflowError("MEDIA_CLEANUP_SYMLINK_DENIED")

    def cleanup(
        self,
        workspace: MediaWorkspace,
        *,
        keep_local=False,
        derived_workspaces=(),
    ) -> dict:
        job = workspace.load()

        if job.state is not MediaWorkflowState.PUBLISHED or not job.publish:
            raise MediaWorkflowError("MEDIA_CLEANUP_PUBLISH_REQUIRED")

        candidate = json.loads(
            workspace.read_artifact("metadata/candidate.json")
        )

        approval = job.approval or {}
        publish = job.publish or {}

        expected_hash = approval.get("output_sha256")

        if (
            not expected_hash
            or candidate.get("output_sha256") != expected_hash
            or publish.get("output_sha256") != expected_hash
        ):
            raise MediaWorkflowError("MEDIA_CLEANUP_PUBLISH_VERIFY_REQUIRED")

        removed = []
        candidate_path = None
        derived_paths = []

        if not keep_local:
            candidate_relative = candidate.get("output_path")

            if (
                not isinstance(candidate_relative, str)
                or not candidate_relative
            ):
                raise MediaWorkflowError(
                    "MEDIA_CLEANUP_CANDIDATE_PATH_INVALID"
                )

            candidate_path = workspace._inside(candidate_relative)

            if candidate_path.is_file():
                if sha256_file(candidate_path) != expected_hash:
                    raise MediaWorkflowError(
                        "MEDIA_CLEANUP_CANDIDATE_CHANGED"
                    )

            self._assert_tree_safe(candidate_path)
            for relative in self.ELIGIBLE_DIRS:
                self._assert_tree_safe(workspace.path / relative)
            for relative in self.ELIGIBLE_FILES:
                self._assert_tree_safe(workspace.path / relative)
            for derived in derived_workspaces:
                derived_path=self._validate_derived_workspace(Path(derived))
                self._assert_tree_safe(derived_path)
                derived_paths.append(derived_path)

        workspace.transition(
            MediaWorkflowState.CLEANUP_PENDING,
            reason="verified publish cleanup",
        )

        if not keep_local:

            if candidate_path is not None and candidate_path.is_file():
                self._remove_file(
                    candidate_path,
                    removed,
                    candidate_relative,
                )

            for relative in self.ELIGIBLE_DIRS:
                self._remove_dir_files(
                    workspace.path / relative,
                    removed,
                    relative,
                )

            for relative in self.ELIGIBLE_FILES:
                self._remove_file(
                    workspace.path / relative,
                    removed,
                    relative,
                )

            for derived_path in derived_paths:
                for relative in self.PRESENTATION_DIRS:
                    self._remove_dir_files(
                        derived_path / relative,
                        removed,
                        f"derived:{derived_path.name}/{relative}",
                    )

                for relative in self.PRESENTATION_FILES:
                    self._remove_file(
                        derived_path / relative,
                        removed,
                        f"derived:{derived_path.name}/{relative}",
                    )

        workspace.write_artifact(
            "metadata/cleanup.json",
            {
                "schema_version": "0.2",
                "keep_local": keep_local,
                "removed": removed,
                "at": utc_now(),
            },
        )

        workspace.transition(
            MediaWorkflowState.ARCHIVED,
            reason="cleanup complete",
        )

        return {
            "removed": removed,
            "retained": [
                "job.json",
                "source_evidence.json",
                "requirements.json",
                "requirements.md",
                "production_brief.md",
                "script.txt",
                "scene_plan.json",
                "prompt_pack.json",
                "metadata/approval.json",
                "metadata/candidate.json",
                "metadata/cleanup.json",
            ],
        }
