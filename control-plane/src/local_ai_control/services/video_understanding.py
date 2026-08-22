"""Native-video boundary with a bounded frame/transcript fallback."""
from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class VideoUnderstandingResult:
    summary:str; timeline:tuple[str,...]; transcript:str|None; method:str
class NativeVideoProvider(Protocol):
    def understand(self,path:str,prompt:str)->VideoUnderstandingResult: ...
class FrameExtractor(Protocol):
    def sample(self,path:str,max_frames:int)->tuple[str,...]: ...
class FrameVisionProvider(Protocol):
    def understand_frames(self,frames:tuple[str,...],prompt:str,transcript:str|None)->VideoUnderstandingResult: ...

class VideoUnderstandingService:
    def __init__(self,native=None,frame_extractor=None,vision=None,stt=None,max_frames=16,max_bytes=100*1024**2):
        self.native=native; self.frame_extractor=frame_extractor; self.vision=vision; self.stt=stt
        self.max_frames=max_frames; self.max_bytes=max_bytes
    def analyze(self,path,prompt,size_bytes):
        if size_bytes<1 or size_bytes>self.max_bytes: raise ValueError("video quota exceeded")
        if self.native:
            try: return self.native.understand(path,prompt)
            except (NotImplementedError,RuntimeError): pass
        if not self.frame_extractor or not self.vision: raise RuntimeError("VIDEO_UNDERSTANDING_NOT_CONFIGURED")
        frames=self.frame_extractor.sample(path,self.max_frames)
        if len(frames)>self.max_frames: raise ValueError("frame sampler exceeded quota")
        transcript=self.stt.transcribe(path).text if self.stt else None
        result=self.vision.understand_frames(frames,prompt,transcript)
        return VideoUnderstandingResult(result.summary,result.timeline,transcript,"FRAME_STT_FALLBACK")
