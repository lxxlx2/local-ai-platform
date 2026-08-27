import json
import os
import wave
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from local_ai_control.services.presentation_jobs import PPTXParser, PresentationError, PresentationJob
from local_ai_control.services.presentation_voice import (
    LanguageDetector, VoiceProfile, VoiceProfileError, VoiceProfileStore, VoiceRouter,
    sha256_file,
)


def make_wav(path: Path, seconds: float = 0.2, rate: int = 16_000):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate)
        wav.writeframes(b"\0\0" * int(seconds * rate))


def profile(path: Path, *, status="QUALIFIED", profile_id="en-male-25-default", language="en"):
    return VoiceProfile(
        "0.1", profile_id, 1, language, "test", 25, "male", "professional",
        "qwen3-tts-voice-design-reference", "test-model", "abc", "reference.wav",
        sha256_file(path), "anchor", "instruction", 16_000, 0.2, status,
        "2026-08-28T00:00:00+00:00" if status == "QUALIFIED" else None,
    )


def make_pptx(path: Path):
    presentation = '''<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="1" r:id="rId1"/></p:sldIdLst></p:presentation>'''
    pres_rels = '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/></Relationships>'''
    slide = '''<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:sp><p:nvSpPr><p:cNvPr id="1" name="Title" descr="alt title"/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>Local AI</a:t></a:r></a:p></p:txBody></p:sp><p:sp><p:nvSpPr><p:cNvPr id="2" name="Body"/><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr><p:txBody><a:p><a:r><a:t>Runs locally.</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'''
    slide_rels = '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide1.xml"/></Relationships>'''
    notes = '''<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:t>Explain local execution.</a:t></p:notes>'''
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/presentation.xml", presentation)
        z.writestr("ppt/_rels/presentation.xml.rels", pres_rels)
        z.writestr("ppt/slides/slide1.xml", slide)
        z.writestr("ppt/slides/_rels/slide1.xml.rels", slide_rels)
        z.writestr("ppt/notesSlides/notesSlide1.xml", notes)


def test_language_detection_and_mixed_policy():
    detector = LanguageDetector()
    assert detector.detect("欢迎观看本次中文演示").language == "zh"
    assert detector.detect("Welcome to this English presentation").language == "en"
    mixed = detector.detect("欢迎观看 our local AI platform 演示")
    assert mixed.language == "mixed" and mixed.warning
    assert detector.detect("123 !").language == "unknown"


def test_profile_store_qualified_hash_and_containment(tmp_path):
    wav = tmp_path / "source.wav"; make_wav(wav)
    store = VoiceProfileStore(tmp_path / "profiles")
    saved = store.save(profile(wav), wav)
    assert saved.profile_id == "en-male-25-default"
    assert oct((tmp_path / "profiles").stat().st_mode & 0o777) == "0o700"
    assert oct((tmp_path / "profiles/en-male-25-default/profile.json").stat().st_mode & 0o777) == "0o600"
    with pytest.raises(VoiceProfileError, match="ALREADY_EXISTS"):
        store.save(profile(wav), wav)
    (tmp_path / "profiles/en-male-25-default/reference.wav").write_bytes(b"bad")
    with pytest.raises(VoiceProfileError): store.load("en-male-25-default")


def test_unqualified_invalid_and_symlink_profiles_fail_closed(tmp_path):
    wav = tmp_path / "source.wav"; make_wav(wav)
    store = VoiceProfileStore(tmp_path / "profiles")
    store.save(profile(wav, status="GENERATED_NOT_QUALIFIED"), wav)
    with pytest.raises(VoiceProfileError, match="NOT_QUALIFIED"):
        store.load("en-male-25-default")
    link = tmp_path / "profiles/evil-profile"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(VoiceProfileError, match="SYMLINK"):
        store.load("evil-profile")


def test_voice_router_overrides_and_unknown(tmp_path):
    wav = tmp_path / "source.wav"; make_wav(wav)
    store = VoiceProfileStore(tmp_path / "profiles")
    store.save(profile(wav), wav)
    router = VoiceRouter(store)
    assert router.route("Welcome to local AI")[1].profile_id == "en-male-25-default"
    assert router.route("123", language="en")[1].profile_id == "en-male-25-default"
    with pytest.raises(VoiceProfileError, match="UNKNOWN"):
        router.route("123")
    with pytest.raises(VoiceProfileError, match="MISMATCH"):
        router.route("欢迎本次演示", profile_id="en-male-25-default")


def test_pptx_parser_extracts_text_notes_and_order(tmp_path):
    source = tmp_path / "deck.pptx"; make_pptx(source)
    slides = PPTXParser().parse(source)
    assert len(slides) == 1
    assert slides[0].title == "Local AI"
    assert slides[0].body == ("Runs locally.",)
    assert slides[0].notes == "Explain local execution."
    assert slides[0].alt_text == ("alt title",)


def test_pptx_rejects_malformed_symlink_and_wrong_suffix(tmp_path):
    bad = tmp_path / "bad.pptx"; bad.write_text("bad")
    with pytest.raises(PresentationError, match="MALFORMED"):
        PPTXParser().parse(bad)
    wrong = tmp_path / "bad.txt"; wrong.write_text("bad")
    with pytest.raises(PresentationError, match="PPTX_ONLY"):
        PPTXParser().parse(wrong)
    link = tmp_path / "link.pptx"; link.symlink_to(bad)
    with pytest.raises(PresentationError, match="SOURCE_INVALID"):
        PPTXParser().parse(link)


def test_private_job_workspace_state_and_source_unchanged(tmp_path):
    source = tmp_path / "deck.pptx"; make_pptx(source)
    before = source.read_bytes()
    job = PresentationJob("job-test-001", tmp_path / "runtime")
    manifest = job.create(source)
    assert manifest["stage"] == "PENDING"
    assert oct((tmp_path / "runtime").stat().st_mode & 0o777) == "0o700"
    job.update_stage("PARSED", slide_count=1)
    assert job.read_json("manifest.json")["slide_count"] == 1
    assert source.read_bytes() == before
    with pytest.raises(PresentationError, match="PATH_ESCAPE"):
        job.write_json("../escape.json", {})
