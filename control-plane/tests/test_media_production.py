import subprocess
import wave
from pathlib import Path

from local_ai_control.services.media_production import (
    DeterministicDeckBuilder, LocalScriptGenerator, MediaPreparationService,
    ScriptDocument, ScriptParser, ScriptScene, StandaloneNarrationAudio,
)
from local_ai_control.services.media_workflow import MediaWorkflowState,new_media_workspace
from local_ai_control.services.presentation_jobs import PPTXParser


class Reply:
    status="completed"; incomplete_reason=None
    def __init__(self,text): self.text=text
class Provider:
    def generate(self,prompt,max_output_tokens): return Reply("## Opening\nWelcome.\n\n## Close\nThank you.")


def test_script_parser_and_local_generator_are_bounded():
    document=ScriptParser().parse("## One\nHello.\n\n## Two\nWorld.",language="en")
    assert len(document.scenes)==2 and document.narration=="Hello.\n\nWorld."
    generated=LocalScriptGenerator(Provider()).generate("Launch a product",language="en")
    assert [scene.title for scene in generated.scenes]==["Opening","Close"]


def test_deterministic_deck_is_valid_for_existing_parser(tmp_path):
    document=ScriptDocument("Launch","en",(ScriptScene(1,"Why","A clear reason."),ScriptScene(2,"How","A safe method.")))
    deck=DeterministicDeckBuilder().build(document,tmp_path/"deck.pptx")
    slides=PPTXParser().parse(deck)
    assert [slide.title for slide in slides]==["Why","How"]
    assert deck.stat().st_mode & 0o777 == 0o600


def test_prepare_persists_script_scene_prompt_pack_and_deck(tmp_path):
    workspace=new_media_workspace("Launch","owner",root=tmp_path)
    workspace.transition(MediaWorkflowState.REQUIREMENTS_PENDING,reason="intake")
    workspace.transition(MediaWorkflowState.REQUIREMENTS_READY,reason="ready")
    result=MediaPreparationService(workspace).prepare(script_text="## Start\nWelcome.\n\n## End\nThanks.",language="en")
    job=workspace.load()
    assert job.state is MediaWorkflowState.PROFILE_SELECTED
    assert {"script.txt","scene_plan.json","prompt_pack.json"} <= set(job.content_hashes)
    assert result["deck"].is_file()


def make_wav(path,seconds=.1):
    with wave.open(str(path),"wb") as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000); out.writeframes(b"\0\0"*int(16000*seconds))


def test_standalone_audio_safe_chunking_transcript_and_timing(tmp_path):
    class Profile:
        profile_id="voice"; profile_revision=2; language="en"; reference_transcript="anchor"
    class Profiles:
        root=tmp_path/"profiles"
        def list(self,language=None): return [Profile()]
        def load(self,profile_id): return Profile()
    (Profiles.root/"voice").mkdir(parents=True); (Profiles.root/"voice"/"reference.wav").write_bytes(b"ref")
    class TTS:
        def synthesize(self,mode,requests,root):
            values=[]
            for request in requests:
                make_wav(Path(request.output)); values.append(type("A",(),{"path":request.output,"duration_seconds":.1})())
            return values
    script=ScriptDocument("x","en",(ScriptScene(1,"a","one"),ScriptScene(2,"b","two")))
    result=StandaloneNarrationAudio(TTS(),Profiles()).build(script,tmp_path/"audio")
    assert Path(result["path"]).is_file() and len(result["timing"])==2
    assert (tmp_path/"audio"/"transcript.txt").read_text().strip()=="one\n\ntwo"
