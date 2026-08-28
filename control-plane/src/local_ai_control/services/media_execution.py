"""Local-first MediaJob execution from durable intake to exact video review."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import shutil

from .media_delivery import MediaApprovalService
from .media_production import (
    LocalScriptGenerator,
    MediaPreparationService,
    ScriptDocument,
    ScriptParser,
)
from .media_workflow import (
    EvidenceIntake,
    MediaWorkflowError,
    MediaWorkflowState,
    MediaWorkspace,
    Requirements,
    RequirementsStore,
    sha256_file,
)
from .presentation_jobs import PPTXParser, PresentationJob
from .presentation_pipeline import (
    NarrationResolver,
    PresentationPipeline,
)
from .presentation_tts import Qwen3TTSRuntime
from .presentation_voice import VoiceProfileStore
from .qwen38_runtime import Qwen38Provider


PROFILE_ROOT = Path("/Users/jerson/AI/runtime/voice-profiles")
PRESENTATION_ROOT = Path("/Users/jerson/AI/runtime/presentation-jobs")
TTS_WORKER = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "presentation-tts-worker.py"
)


@dataclass(frozen=True)
class MediaExecutionResult:
    job_id: str
    state: str
    output_path: str
    output_sha256: str
    duration_seconds: float
    candidate_revision: int
    presentation_job_path: str


@dataclass(frozen=True)
class MediaScriptReviewResult:
    job_id: str
    state: str
    script_text: str
    candidate_revision: int


class FixedNarrator:
    def __init__(self, document: ScriptDocument):
        self.values = iter(
            scene.narration for scene in document.scenes
        )

    def resolve(
        self,
        slide,
        mode,
        *,
        language_hint="auto",
    ):
        try:
            return next(self.values), "media-script"
        except StopIteration as exc:
            raise MediaWorkflowError(
                "SCRIPT_SCENE_COUNT_MISMATCH"
            ) from exc

    def translate(self, text, target_language):
        return NarrationResolver(
            Qwen38Provider(timeout=180)
        ).translate(
            text,
            target_language,
        )


class MediaExecutionService:
    """Executes one durable MediaJob using already-qualified local providers."""

    def __init__(
        self,
        workspace: MediaWorkspace,
        *,
        provider=None,
        profile_root: Path = PROFILE_ROOT,
        presentation_root: Path = PRESENTATION_ROOT,
        tts_worker: Path = TTS_WORKER,
        evidence_intake=None,
    ):
        self.workspace = workspace
        self.provider = provider or Qwen38Provider(timeout=180)
        self.profile_root = Path(profile_root)
        self.presentation_root = Path(presentation_root)
        self.tts_worker = Path(tts_worker)
        self.evidence_intake = (
            evidence_intake or EvidenceIntake()
        )

    def _request(self) -> dict:
        try:
            value = json.loads(
                self.workspace.read_artifact(
                    "metadata/request.json"
                )
            )
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise MediaWorkflowError(
                "MEDIA_REQUEST_METADATA_INVALID"
            ) from exc

        if value.get("schema_version") != "0.2":
            raise MediaWorkflowError(
                "MEDIA_REQUEST_METADATA_INVALID"
            )

        if value.get("language") not in {
            "auto",
            "zh",
            "en",
        }:
            raise MediaWorkflowError(
                "MEDIA_REQUEST_LANGUAGE_INVALID"
            )

        return value

    def _collect_inputs(self, request: dict):
        evidence = []
        brief_parts = []
        script_text = None
        supplied_pptx = None

        for upload in request.get("uploads", []):
            relative = upload.get("path")
            if not isinstance(relative, str):
                raise MediaWorkflowError(
                    "MEDIA_UPLOAD_RECORD_INVALID"
                )

            path = self.workspace._inside(relative)

            if not path.is_file() or path.is_symlink():
                raise MediaWorkflowError(
                    "MEDIA_UPLOAD_RECORD_INVALID"
                )

            suffix = path.suffix.lower()

            if suffix == ".pptx":
                if supplied_pptx is not None:
                    raise MediaWorkflowError(
                        "MEDIA_MULTIPLE_PPTX_UNSUPPORTED"
                    )
                PPTXParser().parse(path)
                supplied_pptx = path
                evidence.append(
                    {
                        "source": upload.get(
                            "name",
                            "presentation.pptx",
                        ),
                        "trust_label": "OWNER_PROVIDED",
                    }
                )
                continue

            if suffix in {".txt", ".md"}:
                value = path.read_text(
                    "utf-8",
                    errors="replace",
                ).strip()

                if not value:
                    raise MediaWorkflowError(
                        "MEDIA_TEXT_INPUT_EMPTY"
                    )

                if script_text is None:
                    script_text = value
                else:
                    brief_parts.append(value)

                evidence.append(
                    {
                        "source": upload.get(
                            "name",
                            path.name,
                        ),
                        "trust_label": "OWNER_PROVIDED",
                    }
                )
                continue

            if suffix == ".docx":
                raise MediaWorkflowError(
                    "MEDIA_DOCX_PARSER_NOT_QUALIFIED"
                )

            if suffix == ".pdf":
                raise MediaWorkflowError("MEDIA_PDF_PARSER_NOT_QUALIFIED")

            raise MediaWorkflowError("MEDIA_BINARY_INPUT_PARSER_NOT_QUALIFIED")

        direct = self.workspace.path / "source" / "direct-brief.txt"

        if direct.is_file() and not direct.is_symlink():
            brief_parts.append(
                direct.read_text(
                    "utf-8",
                    errors="replace",
                ).strip()
            )
            evidence.append(
                {
                    "source": "owner-direct-brief",
                    "trust_label": "OWNER_PROVIDED",
                }
            )

        owner_facts = self.workspace.path / "source" / "owner-facts.txt"
        if owner_facts.is_file() and not owner_facts.is_symlink():
            brief_parts.append(owner_facts.read_text("utf-8",errors="replace").strip())
            evidence.append({"source":"owner-facts","trust_label":"OWNER_PROVIDED"})

        urls = request.get("source_urls", [])

        if len(urls) > 10:
            raise MediaWorkflowError(
                "MEDIA_URL_LIMIT_EXCEEDED"
            )

        for url in urls:
            result = self.evidence_intake.from_url(
                self.workspace,
                url,
            )

            evidence.append(
                result["provenance"]
            )

            body = self.workspace.read_artifact(
                result["artifact"]["path"]
            ).decode(
                "utf-8",
                errors="replace",
            )

            brief_parts.append(
                body[:50_000]
            )

        brief = "\n\n".join(
            part
            for part in brief_parts
            if part
        )

        return {
            "evidence": evidence,
            "brief": brief,
            "script_text": script_text,
            "pptx": supplied_pptx,
        }

    def _persist_requirements(
        self,
        request: dict,
        inputs: dict,
    ):
        job = self.workspace.load()

        if job.state is MediaWorkflowState.RECEIVED:
            self.workspace.transition(
                MediaWorkflowState.REQUIREMENTS_PENDING,
                reason="durable media intake",
            )
        elif job.state is not MediaWorkflowState.REQUIREMENTS_PENDING:
            raise MediaWorkflowError(
                "MEDIA_EXECUTION_STATE_INVALID"
            )

        objective = job.task_name

        if inputs["brief"]:
            objective = (
                inputs["brief"][:1000]
                .replace("\x00", "")
                .strip()
            )

        requirements = Requirements(
            objective=objective,
            language_requirements=request["language"],
            official_references=tuple(
                request.get("source_urls", [])
            ),
        )

        RequirementsStore().persist(
            self.workspace,
            requirements,
            inputs["evidence"],
        )

        self.workspace.transition(
            MediaWorkflowState.REQUIREMENTS_READY,
            reason="requirements persisted",
        )

    def _persist_owner_script(
        self,
        script_text: str,
        language: str,
        pptx: Path,
    ) -> ScriptDocument:
        document = ScriptParser().parse(
            script_text,
            title=self.workspace.load().task_name,
            language=language,
        )

        slides = PPTXParser().parse(pptx)

        if len(document.scenes) != len(slides):
            raise MediaWorkflowError(
                "SCRIPT_SCENE_COUNT_MISMATCH"
            )

        self.workspace.transition(
            MediaWorkflowState.SCRIPT_PENDING,
            reason="owner script preparation",
        )

        script = "\n\n".join(
            f"## {scene.title}\n{scene.narration}"
            for scene in document.scenes
        ) + "\n"

        self.workspace.write_artifact(
            "script.txt",
            script,
        )

        self.workspace.write_artifact(
            "scene_plan.json",
            {
                "schema_version": "0.2",
                "title": document.title,
                "language": document.language,
                "scenes": [
                    asdict(scene)
                    for scene in document.scenes
                ],
            },
        )

        self.workspace.write_artifact(
            "prompt_pack.json",
            {
                "schema_version": "0.2",
                "prompts": [],
                "note": (
                    "Owner-supplied presentation "
                    "and narration script."
                ),
            },
        )

        self.workspace.transition(
            MediaWorkflowState.SCRIPT_READY,
            reason="owner script ready",
        )

        return document

    def _script_review_result(self) -> MediaScriptReviewResult:
        job=self.workspace.load()
        if job.state is not MediaWorkflowState.SCRIPT_READY:
            raise MediaWorkflowError("MEDIA_SCRIPT_REVIEW_STATE_INVALID")
        return MediaScriptReviewResult(job.job_id,job.state.value,
            self.workspace.read_artifact("script.txt").decode("utf-8"),job.candidate_revision)

    def _build_to_review(self, pipeline, presentation, manifest=None) -> MediaExecutionResult:
        if self.workspace.load().state is MediaWorkflowState.SCRIPT_READY:
            self.workspace.transition(MediaWorkflowState.PROFILE_SELECTED,reason="qualified voice selected")
        if manifest is None:
            manifest = pipeline.prepare(
                narration_mode="auto",
                language=self._request()["language"],
                voice_profile=self._request().get("voice","auto"),
            )
        built_manifest=pipeline.build()
        built=presentation.path/built_manifest["output_path"]
        self.workspace.write_artifact("output/final.mp4",built.read_bytes())
        prior_paths=[]
        try:
            prior=json.loads(self.workspace.read_artifact("metadata/execution.json"))
            prior_paths=list(prior.get("presentation_job_paths",[]))
            if prior.get("presentation_job_path"):
                prior_paths.append(prior["presentation_job_path"])
        except (OSError,ValueError,json.JSONDecodeError):
            pass
        presentation_paths=list(dict.fromkeys([*prior_paths,str(presentation.path)]))
        self.workspace.write_artifact("metadata/execution.json",{
            "schema_version":"0.2","presentation_job_id":presentation.path.name,
            "presentation_job_path":str(presentation.path),"profile_id":built_manifest.get("profile_id"),
            "presentation_job_paths":presentation_paths,
            "duration_seconds":built_manifest["duration_seconds"],
            "output_sha256":sha256_file(self.workspace.path/"output"/"final.mp4"),
        })
        for target in (MediaWorkflowState.ASSETS_READY,MediaWorkflowState.AUDIO_READY,
                       MediaWorkflowState.VISUAL_READY,MediaWorkflowState.VIDEO_READY):
            self.workspace.transition(target,reason="local media production")
        candidate=MediaApprovalService().submit_for_review(self.workspace,self.workspace.path/"output"/"final.mp4",
                                                            duration_seconds=built_manifest["duration_seconds"])
        return MediaExecutionResult(self.workspace.path.name,self.workspace.load().state.value,
            str(self.workspace.path/"output"/"final.mp4"),candidate["output_sha256"],
            built_manifest["duration_seconds"],candidate["candidate_revision"],str(presentation.path))

    def resume_after_script_review(self) -> MediaExecutionResult:
        job=self.workspace.load()
        if job.state is not MediaWorkflowState.SCRIPT_READY:
            raise MediaWorkflowError("MEDIA_SCRIPT_REVIEW_RESUME_INVALID")
        request=self._request(); inputs=self._collect_inputs(request)
        deck=self.workspace.path/"generated"/"presentation.pptx"
        if not deck.is_file(): deck=inputs["pptx"]
        if deck is None or not Path(deck).is_file(): raise MediaWorkflowError("MEDIA_PRESENTATION_SOURCE_REQUIRED")
        document=ScriptParser().parse(self.workspace.read_artifact("script.txt").decode("utf-8"),
                                      title=job.task_name,language=request["language"])
        if len(document.scenes) != len(PPTXParser().parse(deck)):
            raise MediaWorkflowError("SCRIPT_SCENE_COUNT_MISMATCH")
        presentation=self._presentation_job(Path(deck)); pipeline=self._pipeline(presentation,FixedNarrator(document))
        narration_path=presentation.path/"narration.json"
        manifest={} if narration_path.is_file() else None
        return self._build_to_review(pipeline,presentation,manifest)

    def _presentation_job(
        self,
        deck: Path,
    ) -> PresentationJob:
        suffix = self.workspace.path.name[-12:]
        revision = self.workspace.load().candidate_revision
        job_id = f"media-{suffix}-r{revision}"

        presentation = PresentationJob(
            job_id,
            self.presentation_root,
        )

        if not presentation.path.exists():
            presentation.create(deck)
            return presentation

        manifest = presentation.path / "manifest.json"

        if (
            not manifest.is_file()
            or presentation.read_json(
                "manifest.json"
            ).get("source_sha256")
            != sha256_file(deck)
        ):
            raise MediaWorkflowError(
                "MEDIA_PRESENTATION_JOB_CONFLICT"
            )

        return presentation

    def _pipeline(
        self,
        presentation: PresentationJob,
        narrator,
    ):
        return PresentationPipeline(
            presentation,
            profile_store=VoiceProfileStore(
                self.profile_root
            ),
            tts=Qwen3TTSRuntime(
                worker=self.tts_worker
            ),
            narrator=narrator,
        )

    def run_to_review(self) -> MediaExecutionResult | MediaScriptReviewResult:
        request = self._request()
        inputs = self._collect_inputs(request)

        self._persist_requirements(
            request,
            inputs,
        )

        language = request["language"]
        voice = request.get("voice", "auto")
        script_review = request.get("completion_mode") == "SCRIPT_REVIEW_FIRST"
        supplied_pptx = inputs["pptx"]
        script_text = inputs["script_text"]

        document = None
        deck = supplied_pptx

        if supplied_pptx is not None and script_text:
            document = self._persist_owner_script(
                script_text,
                language,
                supplied_pptx,
            )

        elif supplied_pptx is None:
            generator = (
                None
                if script_text
                else LocalScriptGenerator(
                    self.provider
                )
            )

            prepared = MediaPreparationService(
                self.workspace,
                script_generator=generator,
            ).prepare(
                script_text=script_text,
                brief_text=inputs["brief"],
                language=language,
                select_profile=False,
            )

            document = prepared["script"]
            deck = prepared["deck"]

        else:
            self.workspace.transition(
                MediaWorkflowState.SCRIPT_PENDING,
                reason="automatic slide narration",
            )

        if deck is None:
            raise MediaWorkflowError(
                "MEDIA_PRESENTATION_SOURCE_REQUIRED"
            )

        if document is not None and script_review:
            return self._script_review_result()

        presentation = self._presentation_job(
            deck
        )

        if document is not None:
            narrator = FixedNarrator(document)
        else:
            narrator = NarrationResolver(
                self.provider
            )

        pipeline = self._pipeline(
            presentation,
            narrator,
        )

        narration = pipeline.prepare(
            narration_mode="auto",
            language=language,
            voice_profile=voice,
        )

        if document is None:
            values = narration["slides"]

            script = "\n\n".join(
                (
                    f"## Slide {item['slide']}\n"
                    f"{item['text']}"
                )
                for item in values
            ) + "\n"

            self.workspace.write_artifact(
                "script.txt",
                script,
            )

            self.workspace.write_artifact(
                "scene_plan.json",
                {
                    "schema_version": "0.2",
                    "language": language,
                    "scenes": values,
                },
            )

            self.workspace.write_artifact(
                "prompt_pack.json",
                {
                    "schema_version": "0.2",
                    "prompts": [],
                    "note": (
                        "Narration generated from "
                        "Owner-supplied PPTX."
                    ),
                },
            )

            self.workspace.transition(
                MediaWorkflowState.SCRIPT_READY,
                reason="automatic narration ready",
            )
            if script_review:
                return self._script_review_result()

        return self._build_to_review(pipeline,presentation,narration)
