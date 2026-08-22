import pytest
from local_ai_control.domain.identity import Role
from local_ai_control.services.audio import AudioService, SpeechArtifact, Transcript
class STT:
    def transcribe(self,path): return Transcript("hello","en",1.2)
class TTS:
    def synthesize(self,text,reference_audio=None,style=None): return SpeechArtifact("/private/out.wav",2,100)
def test_audio_service_enforces_quota_and_transcribes():
    service=AudioService(STT(),TTS(),max_audio_bytes=10)
    assert service.transcribe("/private/a.wav",10).text=="hello"
    with pytest.raises(ValueError): service.transcribe("/private/a.wav",11)
def test_voice_generation_and_clone_are_owner_only():
    service=AudioService(STT(),TTS())
    with pytest.raises(PermissionError): service.synthesize(Role.PUBLIC,"hello")
    assert service.synthesize(Role.OWNER,"hello",reference_audio="private-ref").size_bytes==100
