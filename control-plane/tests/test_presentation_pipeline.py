import json
import subprocess
import wave
import zipfile
from pathlib import Path

import pytest

from local_ai_control.services.presentation_jobs import ParsedSlide, PresentationError
from local_ai_control.services.presentation_pipeline import (
    NarrationResolver, TimelineBuilder, VideoComposer, stable_hash,
    PresentationPipeline,
)
from local_ai_control.services.presentation_jobs import PresentationJob
from local_ai_control.services.presentation_voice import VoiceProfile, VoiceProfileStore, sha256_file


class Reply:
    def __init__(self, text, status="completed", incomplete_reason=None):
        self.text=text; self.status=status; self.incomplete_reason=incomplete_reason
class Provider:
    def __init__(self, reply): self.reply=reply; self.calls=[]
    def generate(self,prompt,max_output_tokens): self.calls.append((prompt,max_output_tokens)); return self.reply


SLIDE = ParsedSlide(1,"Local AI",("Runs locally",),(),"",(),())


def make_wav(path, seconds, rate=16000):
    with wave.open(str(path),"wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate)
        wav.writeframes(b"\0\0"*int(seconds*rate))


def make_minimal_pptx(path):
    presentation='''<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="1" r:id="rId1"/></p:sldIdLst></p:presentation>'''
    relationships='''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/></Relationships>'''
    slide='''<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:sp><p:nvSpPr><p:cNvPr id="1" name="Title"/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>Local AI</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'''
    with zipfile.ZipFile(path,"w") as archive:
        archive.writestr("ppt/presentation.xml",presentation)
        archive.writestr("ppt/_rels/presentation.xml.rels",relationships)
        archive.writestr("ppt/slides/slide1.xml",slide)


def test_narration_modes_notes_missing_and_bounded_local_generation():
    with pytest.raises(PresentationError,match="NOTES_MISSING"):
        NarrationResolver().resolve(SLIDE,"notes")
    slide=ParsedSlide(1,"Title",(),(),"Use these notes.",(),())
    assert NarrationResolver().resolve(slide,"hybrid")== ("Use these notes.","notes")
    provider=Provider(Reply("**Clear narration.**"))
    assert NarrationResolver(provider).resolve(SLIDE,"auto",language_hint="en")== ("Clear narration.","local-qwen38")
    assert "untrusted data" in provider.calls[0][0]


def test_incomplete_narration_fails_closed():
    with pytest.raises(PresentationError,match="INCOMPLETE"):
        NarrationResolver(Provider(Reply("half",status="incomplete",incomplete_reason="length"))).resolve(SLIDE,"auto")


def test_translation_requires_explicit_target_and_local_completed_provider():
    provider=Provider(Reply("Natural translated narration."))
    assert NarrationResolver(provider).translate("欢迎", "en") == "Natural translated narration."
    assert "untrusted narration data" in provider.calls[0][0]
    with pytest.raises(PresentationError,match="TARGET_LANGUAGE"):
        NarrationResolver().translate("hello","zh")


def test_timeline_uses_actual_wav_duration(tmp_path):
    first=tmp_path/"a.wav"; second=tmp_path/"b.wav"
    make_wav(first,1.0); make_wav(second,2.0)
    result=TimelineBuilder().build([first,second])
    assert result["slides"][0]["duration_seconds"]==1.55
    assert result["slides"][1]["start_seconds"]==1.55
    assert result["total_duration_seconds"]==4.1


def test_video_composer_uses_fixed_shell_false_allowlisted_invocation(tmp_path):
    image=tmp_path/"slide.png"; image.write_bytes(b"png")
    audio=tmp_path/"audio.wav"; make_wav(audio,1)
    output=tmp_path/"out.mp4"; calls=[]
    def run(argv,**kwargs):
        calls.append((argv,kwargs)); output.write_bytes(b"mp4")
        return subprocess.CompletedProcess(argv,0,"","")
    ffmpeg=tmp_path/"ffmpeg"; ffprobe=tmp_path/"ffprobe"; ffmpeg.write_text("x"); ffprobe.write_text("x")
    composer=VideoComposer(ffmpeg=str(ffmpeg),ffprobe=str(ffprobe),run=run)
    composer.segment(image,audio,{"lead_seconds":.15,"duration_seconds":1.55},output)
    assert calls[0][1]["shell"] is False
    assert calls[0][0][0]==str(ffmpeg)
    assert any("adelay=150|150" in value for value in calls[0][0])


def test_stable_hash_is_deterministic_and_sensitive():
    assert stable_hash({"a":1,"b":2})==stable_hash({"b":2,"a":1})
    assert stable_hash({"a":1})!=stable_hash({"a":2})


def test_pipeline_resumes_and_user_script_edit_regenerates_only_dependency(tmp_path):
    source=tmp_path/"deck.pptx"; make_minimal_pptx(source)
    job=PresentationJob("presentation-cache-test",tmp_path/"jobs"); job.create(source)
    reference=tmp_path/"reference.wav"; make_wav(reference,.2)
    store=VoiceProfileStore(tmp_path/"profiles")
    store.save(VoiceProfile(
        "0.1","en-male-25-default",1,"en","test",25,"male","professional",
        "qwen3-tts-voice-design-reference","model","revision","reference.wav",
        sha256_file(reference),"anchor","instruction",16000,.2,"QUALIFIED","2026-08-28T00:00:00+00:00",
    ),reference)
    class Renderer:
        def render(self,source,output_dir,expected_count):
            output_dir.mkdir(exist_ok=True); page=output_dir/"slide-1.png"; page.write_bytes(b"png"); return [page]
    class TTS:
        def __init__(self): self.calls=[]
        def synthesize(self,mode,requests,root):
            self.calls.append([request.text for request in requests])
            for request in requests: make_wav(Path(request.output),1)
    class Composer:
        def __init__(self): self.segments=0
        def segment(self,image,audio,timeline,output): self.segments+=1; output.write_bytes(b"segment")
        def concatenate(self,segments,output): output.write_bytes(b"video")
        def duration(self,output): return 1.55
    tts=TTS(); composer=Composer()
    pipeline=PresentationPipeline(job,profile_store=store,tts=tts,narrator=NarrationResolver(Provider(Reply("Natural English narration."))),renderer=Renderer(),composer=composer)
    pipeline.prepare(narration_mode="auto")
    pipeline.build(); assert len(tts.calls)==1 and composer.segments==1
    pipeline.build(); assert len(tts.calls)==1 and composer.segments==1
    narration=job.read_json("narration.json"); narration["slides"][0]["text"]="Edited narration."
    job.write_json("narration.json",narration)
    pipeline.build()
    assert len(tts.calls)==2 and tts.calls[-1]==["Edited narration."] and composer.segments==2
