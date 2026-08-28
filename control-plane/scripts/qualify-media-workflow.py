#!/usr/bin/env python3
"""One bounded real-local Media Product Workflow V0.2 qualification run."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from local_ai_control.services.media_delivery import MediaApprovalService,MediaCleanup,MediaPublisher
from local_ai_control.services.media_production import LocalScriptGenerator,MediaPreparationService
from local_ai_control.services.media_workflow import MediaWorkflowState,Requirements,RequirementsStore,new_media_workspace
from local_ai_control.services.presentation_jobs import PresentationJob
from local_ai_control.services.presentation_pipeline import PresentationPipeline
from local_ai_control.services.presentation_tts import Qwen3TTSRuntime
from local_ai_control.services.presentation_voice import VoiceProfileStore
from local_ai_control.services.qwen38_runtime import Qwen38Provider


PROFILE_ROOT=Path("/Users/jerson/AI/runtime/voice-profiles")
WORKER=Path(__file__).with_name("presentation-tts-worker.py")


class FixedNarrator:
    def __init__(self,scenes): self.values=iter(scene.narration for scene in scenes)
    def resolve(self,slide,mode,*,language_hint="auto"):
        try: return next(self.values),"media-script"
        except StopIteration as exc: raise RuntimeError("SCRIPT_SCENE_COUNT_MISMATCH") from exc
    def translate(self,text,target_language): raise RuntimeError("TRANSLATION_NOT_EXPECTED")


def git(*args,cwd): return subprocess.run(["git",*args],cwd=cwd,check=True,capture_output=True,text=True)


def temp_public_repo(root:Path):
    remote=root/"remote.git"; git("init","--bare",str(remote),cwd=root)
    repo=root/"public"; git("init",str(repo),cwd=root)
    git("config","user.email","qualification@example.invalid",cwd=repo); git("config","user.name","Media Qualification",cwd=repo)
    git("remote","add","origin",str(remote),cwd=repo)
    (repo/".gitattributes").write_text("*.mp4 filter=lfs diff=lfs merge=lfs -text\n","utf-8")
    (repo/"README.md").write_text("# Qualification Products\n","utf-8")
    git("add",".",cwd=repo); git("commit","-m","init",cwd=repo); git("push","-u","origin","HEAD",cwd=repo)
    return repo,remote


def run(root:Path)->dict:
    workspace=new_media_workspace("Media Workflow Qualification","qualification-owner",root=root/"media-jobs")
    brief=("Create a concise English presentation explaining a local-first media workflow. "
           "Cover durable intake, reviewed generation, exact-output approval, and verified publishing. "
           "Use three short scenes and a professional, factual tone.")
    workspace.transition(MediaWorkflowState.REQUIREMENTS_PENDING,reason="qualification brief")
    RequirementsStore().persist(workspace,Requirements("Explain the safe local-first media production workflow",language_requirements="en",duration_constraints="under one minute"),[{"source":"synthetic qualification brief","trust_label":"TEST_FIXTURE"}])
    workspace.transition(MediaWorkflowState.REQUIREMENTS_READY,reason="qualification requirements ready")
    prepared=MediaPreparationService(workspace,script_generator=LocalScriptGenerator(Qwen38Provider(timeout=180))).prepare(brief_text=brief,language="en")
    presentation=PresentationJob("presentation-media-v02",root/"presentation-jobs"); presentation.create(prepared["deck"])
    pipeline=PresentationPipeline(presentation,profile_store=VoiceProfileStore(PROFILE_ROOT),
        tts=Qwen3TTSRuntime(worker=WORKER),narrator=FixedNarrator(prepared["script"].scenes))
    pipeline.prepare(narration_mode="auto",language="en",voice_profile="en-male-25-default")
    manifest=pipeline.build()
    workspace.write_artifact("output/final.mp4",(presentation.path/manifest["output_path"]).read_bytes())
    for target in (MediaWorkflowState.ASSETS_READY,MediaWorkflowState.AUDIO_READY,MediaWorkflowState.VISUAL_READY,MediaWorkflowState.VIDEO_READY):
        workspace.transition(target,reason="real local qualification")
    candidate=MediaApprovalService().submit_for_review(workspace,workspace.path/"output"/"final.mp4",duration_seconds=manifest["duration_seconds"])
    MediaApprovalService().approve(workspace,owner_id="qualification-owner",**candidate)
    repo,remote=temp_public_repo(root)
    published=MediaPublisher(repo,expected_remote=str(remote)).publish(workspace)
    cleanup=MediaCleanup().cleanup(workspace)
    return {"status":"PASS","job_id":workspace.path.name,"state":workspace.load().state,
            "scene_count":len(prepared["script"].scenes),"deck":str(prepared["deck"]),
            "slide_pngs":[str(item) for item in sorted((presentation.path/"slides").glob("slide-*.png"))],
            "video":str(workspace.path/"output"/"final.mp4"),"video_sha256":candidate["output_sha256"],
            "duration_seconds":manifest["duration_seconds"],"voice_profile_id":"en-male-25-default",
            "publish_commit":published["commit"],"publish_remote":"TEMP_LOCAL_BARE","cleanup_removed":len(cleanup["removed"]),
            "root":str(root)}


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--root",type=Path)
    args=parser.parse_args();
    if args.root: args.root.mkdir(mode=0o700,parents=True,exist_ok=True); result=run(args.root)
    else:
        with tempfile.TemporaryDirectory(prefix="media-v02-qualification-") as temporary: result=run(Path(temporary))
    print(json.dumps(result,ensure_ascii=False,indent=2,default=str))


if __name__=="__main__": main()
