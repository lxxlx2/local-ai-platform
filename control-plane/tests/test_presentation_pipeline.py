import json
import subprocess
import wave
from pathlib import Path

import pytest

from local_ai_control.services.presentation_jobs import ParsedSlide, PresentationError
from local_ai_control.services.presentation_pipeline import (
    NarrationResolver, TimelineBuilder, VideoComposer, stable_hash,
)


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
