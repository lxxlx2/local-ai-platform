#!/usr/bin/env python3
"""Owner CLI for local persistent-voice narrated presentation videos."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid
from dataclasses import asdict
from pathlib import Path

from local_ai_control.services.presentation_jobs import PPTXParser, PresentationJob
from local_ai_control.services.presentation_pipeline import NarrationResolver, PresentationPipeline
from local_ai_control.services.presentation_tts import DefaultVoiceBootstrap, Qwen3TTSRuntime
from local_ai_control.services.presentation_voice import (
    LanguageDetector, VoiceProfile, VoiceProfileError, VoiceProfileStore, sha256_file, utc_now,
    validate_wav_quality,
)
from local_ai_control.services.qwen38_runtime import Qwen38Provider
from local_ai_control.services.media_workflow import (
    CompletionMode, EvidenceIntake, IntakeMode, MediaWorkflowState, Requirements,
    RequirementsStore, new_media_workspace,
)
from local_ai_control.services.media_production import LocalScriptGenerator, MediaPreparationService, ScriptParser


RUNTIME_ROOT = Path("/Users/jerson/AI/runtime/presentation-jobs")
PROFILE_ROOT = Path("/Users/jerson/AI/runtime/voice-profiles")
WORKER = Path(__file__).with_name("presentation-tts-worker.py")


def runtime() -> Qwen3TTSRuntime:
    return Qwen3TTSRuntime(worker=WORKER)


def print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def new_job(input_path: Path, job_id: str | None) -> PresentationJob:
    identifier = job_id or f"presentation-{uuid.uuid4().hex[:12]}"
    job = PresentationJob(identifier, RUNTIME_ROOT)
    job.create(input_path)
    return job


def owner_reference_profile(job: PresentationJob, source: Path, language: str) -> tuple[VoiceProfileStore, str]:
    if source.is_symlink() or not source.is_file():
        raise VoiceProfileError("VOICE_REFERENCE_INVALID")
    quality = validate_wav_quality(source)
    store = VoiceProfileStore(job.path / "private-voice-profiles")
    profile_id = "job-owner-reference"
    profile = VoiceProfile(
        "0.1", profile_id, 1, language, "Owner-provided job-scoped voice reference",
        None, "owner-provided", "owner-reference", "owner-reference", "owner-local-reference",
        "job-scoped-v0.1", "reference.wav", sha256_file(source), "",
        "No VoiceDesign; explicit Owner reference", quality["sample_rate"], quality["duration_seconds"],
        "QUALIFIED", utc_now(),
    )
    store.save(profile, source)
    return store, profile_id


def pipeline_for(job: PresentationJob, *, profile_store: VoiceProfileStore | None = None, narrator=None) -> PresentationPipeline:
    return PresentationPipeline(
        job, profile_store=profile_store or VoiceProfileStore(PROFILE_ROOT), tts=runtime(),
        narrator=narrator or NarrationResolver(Qwen38Provider(timeout=180)),
    )


def prepare(args) -> dict:
    job = new_job(Path(args.input), args.job_id)
    store = VoiceProfileStore(PROFILE_ROOT); profile_id = args.voice_profile
    if args.voice_reference:
        if args.voice_profile != "auto":
            raise ValueError("--voice-reference and --voice-profile cannot be combined")
        reference_language = args.language
        if reference_language == "auto":
            slides = PPTXParser().parse(args.input)
            decision = LanguageDetector().detect("\n".join(slide.source_text for slide in slides))
            reference_language = decision.dominant_language
        if reference_language not in {"zh", "en"}:
            raise ValueError("VOICE_REFERENCE_LANGUAGE_OVERRIDE_REQUIRED")
        store, profile_id = owner_reference_profile(job, Path(args.voice_reference), reference_language)
    narrator = None
    if getattr(args, "script_file", None):
        document = ScriptParser().parse(Path(args.script_file).read_text("utf-8"), language=args.language)
        values = iter(scene.narration for scene in document.scenes)
        class FileNarrator:
            def resolve(self, slide, mode, *, language_hint="auto"):
                try: return next(values), "owner-script"
                except StopIteration as exc: raise ValueError("SCRIPT_SCENE_COUNT_MISMATCH") from exc
            def translate(self, text, target_language): return NarrationResolver(Qwen38Provider(timeout=180)).translate(text,target_language)
        narrator = FileNarrator()
    narration = pipeline_for(job, profile_store=store, narrator=narrator).prepare(
        narration_mode=args.narration, language=args.language, voice_profile=profile_id,
        target_language=args.target_language, mixed_language_mode=args.mixed_language_mode,
    )
    return {"job_id": job.path.name, "job_path": str(job.path), "narration": narration}


def copy_output(source: Path, output: str | None) -> str:
    if not output:
        return str(source)
    target = Path(output).expanduser()
    if target.suffix.lower() != ".mp4" or target.is_symlink() or not target.parent.is_dir():
        raise ValueError("OUTPUT_PATH_INVALID")
    shutil.copyfile(source, target, follow_symlinks=False)
    os.chmod(target, 0o600)
    return str(target)


def voice_command(args) -> int:
    store = VoiceProfileStore(PROFILE_ROOT); bootstrap = DefaultVoiceBootstrap(runtime(), store)
    if args.voice_action == "status":
        result = {}
        for profile_id in ("zh-male-25-default", "en-male-25-default"):
            try:
                result[profile_id] = asdict(store.load(profile_id, require_qualified=False))
            except VoiceProfileError as exc:
                result[profile_id] = {"status": "MISSING", "error": str(exc)}
        print_json(result); return 0
    if args.voice_action == "create-defaults":
        print_json({key: asdict(value) for key, value in bootstrap.create_defaults().items()}); return 0
    if args.voice_action == "inspect":
        print_json(asdict(store.load(args.profile, require_qualified=False))); return 0
    if args.voice_action == "qualify":
        print_json(asdict(bootstrap.qualify(args.profile))); return 0
    raise ValueError("VOICE_ACTION_INVALID")


def presentation_command(args) -> int:
    if args.presentation_action == "inspect":
        print_json([asdict(slide) for slide in PPTXParser().parse(args.input)]); return 0
    if args.presentation_action in {"prepare", "build", "qualify"}:
        result = prepare(args)
        if args.presentation_action == "prepare":
            print_json(result); return 0
        job = PresentationJob(result["job_id"], RUNTIME_ROOT)
        root = Path(result["narration"]["profile_store_root"])
        manifest = pipeline_for(job, profile_store=VoiceProfileStore(root)).build()
        canonical = job.path / manifest["output_path"]
        manifest["owner_output"] = copy_output(canonical, args.output)
        print_json(manifest); return 0
    job = PresentationJob(args.job_id, RUNTIME_ROOT)
    if args.presentation_action == "status":
        print_json(job.read_json("manifest.json")); return 0
    if args.presentation_action == "resume":
        narration = job.read_json("narration.json")
        manifest = pipeline_for(job, profile_store=VoiceProfileStore(narration["profile_store_root"])).build()
        manifest["owner_output"] = copy_output(job.path / manifest["output_path"], args.output)
        print_json(manifest); return 0
    raise ValueError("PRESENTATION_ACTION_INVALID")


def media_command(args) -> int:
    if args.media_action != "prepare": raise ValueError("MEDIA_ACTION_INVALID")
    provided = sum(bool(value) for value in (args.script_file,args.brief_file,args.url))
    if provided != 1: raise ValueError("MEDIA_SOURCE_EXACTLY_ONE_REQUIRED")
    mode = IntakeMode.LINKS if args.url else IntakeMode.DIRECT_BRIEF
    workspace = new_media_workspace(args.task, "cli-owner", intake_mode=mode,
                                    completion_mode=CompletionMode(args.completion_mode))
    evidence=[]; script_text=None; brief_text=None
    if args.script_file:
        script_text=Path(args.script_file).read_text("utf-8")
        evidence.append({"source":"owner-script","trust_label":"OWNER_PROVIDED"})
    elif args.brief_file:
        brief_text=Path(args.brief_file).read_text("utf-8")
        evidence.append(EvidenceIntake().from_brief(workspace,brief_text)["provenance"])
    else:
        item=EvidenceIntake().from_url(workspace,args.url); evidence.append(item["provenance"])
        brief_text=workspace.read_artifact(item["artifact"]["path"]).decode("utf-8",errors="replace")
    workspace.transition(MediaWorkflowState.REQUIREMENTS_PENDING,reason="CLI intake")
    RequirementsStore().persist(workspace,Requirements(args.task,language_requirements=args.language),evidence)
    workspace.transition(MediaWorkflowState.REQUIREMENTS_READY,reason="requirements persisted")
    generator = LocalScriptGenerator(Qwen38Provider(timeout=180)) if not script_text else None
    result=MediaPreparationService(workspace,script_generator=generator).prepare(script_text=script_text,brief_text=brief_text,language=args.language)
    print_json({"job_id":workspace.path.name,"job_path":str(workspace.path),"state":workspace.load().state,"deck":str(result["deck"])})
    return 0


def common_build_args(parser):
    parser.add_argument("--input", required=True)
    parser.add_argument("--job-id")
    parser.add_argument("--narration", choices=("notes", "auto", "hybrid"), default="hybrid")
    parser.add_argument("--language", choices=("auto", "zh", "en"), default="auto")
    parser.add_argument("--voice-profile", default="auto")
    parser.add_argument("--voice-reference")
    parser.add_argument("--target-language", choices=("zh", "en"))
    parser.add_argument("--mixed-language-mode", choices=("dominant", "per-slide"), default="dominant")
    parser.add_argument("--output")
    parser.add_argument("--script-file")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="presentation-video.sh")
    commands = root.add_subparsers(dest="command", required=True)
    voice = commands.add_parser("voice"); voices = voice.add_subparsers(dest="voice_action", required=True)
    voices.add_parser("status"); voices.add_parser("create-defaults")
    for name in ("inspect", "qualify"):
        sub = voices.add_parser(name); sub.add_argument("profile")
    presentation = commands.add_parser("presentation")
    actions = presentation.add_subparsers(dest="presentation_action", required=True)
    inspect = actions.add_parser("inspect"); inspect.add_argument("--input", required=True)
    for name in ("prepare", "build", "qualify"):
        common_build_args(actions.add_parser(name))
    for name in ("resume", "status"):
        sub = actions.add_parser(name); sub.add_argument("--job-id", required=True)
        if name == "resume": sub.add_argument("--output")
    media=commands.add_parser("media"); media_actions=media.add_subparsers(dest="media_action",required=True)
    media_prepare=media_actions.add_parser("prepare"); media_prepare.add_argument("--task",required=True)
    media_prepare.add_argument("--script-file"); media_prepare.add_argument("--brief-file"); media_prepare.add_argument("--url")
    media_prepare.add_argument("--script-generator",choices=("local-qwen",),default="local-qwen")
    media_prepare.add_argument("--language",choices=("auto","zh","en"),default="auto")
    media_prepare.add_argument("--completion-mode",choices=("AUTO_COMPLETE","SCRIPT_REVIEW_FIRST"),default="AUTO_COMPLETE")
    return root


def main() -> int:
    try:
        args = parser().parse_args()
        if args.command == "voice": return voice_command(args)
        if args.command == "media": return media_command(args)
        return presentation_command(args)
    except Exception as exc:
        print_json({"status": "FAILED", "error_category": type(exc).__name__, "detail": str(exc)[:500]})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
