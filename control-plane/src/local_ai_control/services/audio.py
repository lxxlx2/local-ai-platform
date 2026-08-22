"""Dependency-isolated STT/TTS contracts with conservative quotas."""
from dataclasses import dataclass
from typing import Protocol
from local_ai_control.domain.identity import Role

@dataclass(frozen=True)
class Transcript: text:str; language:str|None; duration_seconds:float; timestamps:tuple=()
@dataclass(frozen=True)
class SpeechArtifact: path:str; duration_seconds:float; size_bytes:int
class STTProvider(Protocol):
    def transcribe(self,path:str)->Transcript: ...
class TTSProvider(Protocol):
    def synthesize(self,text:str,reference_audio:str|None=None,style:str|None=None)->SpeechArtifact: ...

class AudioService:
    def __init__(self,stt=None,tts=None,max_audio_bytes=50*1024**2,max_text_chars=5000,max_output_seconds=600):
        self.stt=stt; self.tts=tts; self.max_audio_bytes=max_audio_bytes; self.max_text_chars=max_text_chars; self.max_output_seconds=max_output_seconds
    def transcribe(self,path,size_bytes):
        if not self.stt: raise RuntimeError("STT_NOT_CONFIGURED")
        if size_bytes<1 or size_bytes>self.max_audio_bytes: raise ValueError("audio quota exceeded")
        return self.stt.transcribe(path)
    def synthesize(self,role,text,*,reference_audio=None,style=None):
        if role is not Role.OWNER: raise PermissionError("TTS is owner-only")
        if not self.tts: raise RuntimeError("TTS_NOT_CONFIGURED")
        if not text.strip() or len(text)>self.max_text_chars: raise ValueError("text quota exceeded")
        result=self.tts.synthesize(text,reference_audio,style)
        if result.duration_seconds>self.max_output_seconds: raise ValueError("speech duration quota exceeded")
        return result
