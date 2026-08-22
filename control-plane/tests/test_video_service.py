import pytest
from local_ai_control.domain.identity import Role
from local_ai_control.services.generative_media import VideoRequest,VideoService
from local_ai_control.services.video_understanding import VideoUnderstandingResult,VideoUnderstandingService
class Provider:
    def generate(self,request): return ("/private/output.mp4",)
def test_video_generation_is_owner_only_and_bounded():
    service=VideoService(Provider())
    assert service.submit(Role.OWNER,VideoRequest("waves"))
    with pytest.raises(PermissionError): service.submit(Role.PUBLIC,VideoRequest("waves"))
    with pytest.raises(ValueError): service.submit(Role.OWNER,VideoRequest("waves",frames=1000))

class Frames:
    def sample(self,path,max_frames): return ("frame:1","frame:2")
class Vision:
    def understand_frames(self,frames,prompt,transcript): return VideoUnderstandingResult("summary",("00:01",),transcript,"vision")
class STT:
    def transcribe(self,path): return type("T",(),{"text":"spoken"})()
def test_video_understanding_bounded_fallback_combines_frames_and_transcript():
    result=VideoUnderstandingService(frame_extractor=Frames(),vision=Vision(),stt=STT()).analyze("private.mp4","summarize",100)
    assert result.method=="FRAME_STT_FALLBACK" and result.transcript=="spoken"
