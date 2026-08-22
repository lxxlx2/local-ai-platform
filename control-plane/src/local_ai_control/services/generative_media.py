"""Owner-only image/video provider boundaries; execution belongs to MediaJobRunner."""
from dataclasses import dataclass
from typing import Protocol
from local_ai_control.domain.identity import Role

@dataclass(frozen=True)
class ImageRequest: prompt:str; width:int=512; height:int=512; input_refs:tuple[str,...]=()
@dataclass(frozen=True)
class VideoRequest: prompt:str; width:int=480; height:int=480; frames:int=16; input_ref:str|None=None
class ImageProvider(Protocol):
    def generate(self,request:ImageRequest)->tuple[str,...]: ...
class VideoProvider(Protocol):
    def generate(self,request:VideoRequest)->tuple[str,...]: ...

class ImageService:
    def __init__(self,provider=None,max_pixels=1024*1024): self.provider=provider; self.max_pixels=max_pixels
    def submit(self,role,request):
        if role is not Role.OWNER: raise PermissionError("image generation is owner-only")
        if not self.provider: raise RuntimeError("IMAGE_PROVIDER_NOT_CONFIGURED")
        if not request.prompt.strip() or request.width<64 or request.height<64 or request.width*request.height>self.max_pixels: raise ValueError("invalid image request")
        return self.provider.generate(request)
class VideoService:
    def __init__(self,provider=None,max_pixels=640*640,max_frames=48): self.provider=provider; self.max_pixels=max_pixels; self.max_frames=max_frames
    def submit(self,role,request):
        if role is not Role.OWNER: raise PermissionError("video generation is owner-only")
        if not self.provider: raise RuntimeError("VIDEO_PROVIDER_NOT_CONFIGURED")
        if not request.prompt.strip() or request.width*request.height>self.max_pixels or not 1<=request.frames<=self.max_frames: raise ValueError("invalid video request")
        return self.provider.generate(request)
