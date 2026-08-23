"""Localhost-only Qwen3.8 text/vision provider and isolated sidecar engine."""
from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import threading
import urllib.error
import urllib.request

from local_ai_control.services.omlx import ModelReply, extract_text

MODEL_ID="mlx-community/Qwen3.8-27B-8bit"
MODEL_PATH=Path("/Users/jerson/AI/models/qwen38-27b-8bit")
DEFAULT_PORT=8001
MAX_CONTEXT_TOKENS=16384
PRIVATE_SPOOL_ROOT=Path("/Users/jerson/AI/runtime/private-media")


class ContextLimitExceeded(ValueError): pass
class RuntimeUnavailable(RuntimeError): pass


class Qwen38Provider:
    def __init__(self,port=DEFAULT_PORT,*,max_context_tokens=MAX_CONTEXT_TOKENS,spool_root=PRIVATE_SPOOL_ROOT,timeout=120):
        self.base_url=f"http://127.0.0.1:{int(port)}"; self.max_context_tokens=max_context_tokens
        self.spool_root=Path(spool_root).resolve(); self.timeout=timeout

    def _request(self,path,payload=None,timeout=None):
        data=None if payload is None else json.dumps(payload,ensure_ascii=False).encode()
        request=urllib.request.Request(self.base_url+path,data=data,headers={"Content-Type":"application/json"})
        try:
            with urllib.request.urlopen(request,timeout=timeout or self.timeout) as response: return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code==413: raise ContextLimitExceeded("prompt exceeds MAIN 16K context limit") from exc
            raise RuntimeUnavailable(f"qwen38 sidecar HTTP {exc.code}") from exc
        except (OSError,urllib.error.URLError,json.JSONDecodeError) as exc:
            raise RuntimeUnavailable("qwen38 sidecar unavailable") from exc

    def health(self): return self._request("/health",timeout=5)

    def _precheck(self,prompt):
        if not isinstance(prompt,str) or not prompt.strip(): raise ValueError("prompt required")
        # Conservative client boundary; the sidecar applies the exact tokenizer
        # limit after its chat template. This rejects safely without inference.
        if len(prompt)>self.max_context_tokens: raise ContextLimitExceeded("prompt exceeds MAIN 16K context limit")

    def generate(self,prompt,max_output_tokens=1024):
        self._precheck(prompt)
        raw=self._request("/v1/responses",{"model":MODEL_ID,"input":prompt,"max_output_tokens":min(int(max_output_tokens),4096)})
        incomplete=raw.get("incomplete_details") or {}
        return ModelReply(extract_text(raw),raw.get("status"),incomplete.get("reason") if isinstance(incomplete,dict) else None,(raw.get("usage") or {}).get("output_tokens"),raw.get("max_output_tokens",max_output_tokens))

    def vision(self,image_path,prompt,max_output_tokens=1024):
        self._precheck(prompt)
        path=Path(image_path).resolve(strict=True)
        if self.spool_root not in path.parents or not path.is_file() or path.stat().st_size>20*1024**2:
            raise PermissionError("vision input outside private spool")
        raw=self._request("/v1/vision",{"model":MODEL_ID,"input":prompt,"image_ref":str(path),"max_output_tokens":min(int(max_output_tokens),2048)})
        incomplete=raw.get("incomplete_details") or {}
        return ModelReply(extract_text(raw),raw.get("status"),incomplete.get("reason") if isinstance(incomplete,dict) else None,(raw.get("usage") or {}).get("output_tokens"),raw.get("max_output_tokens",max_output_tokens))


class Qwen38SidecarEngine:
    def __init__(self,model_path=MODEL_PATH,*,max_context_tokens=MAX_CONTEXT_TOKENS,spool_root=PRIVATE_SPOOL_ROOT):
        from mlx_vlm import load,stream_generate
        from mlx_vlm.prompt_utils import apply_chat_template
        from mlx_vlm.utils import load_config
        self.model,self.processor=load(str(model_path)); self.config=load_config(str(model_path))
        self.stream_generate=stream_generate; self.apply_chat_template=apply_chat_template
        self.max_context_tokens=max_context_tokens; self.spool_root=Path(spool_root).resolve(); self.lock=threading.Lock()

    def _generate(self,prompt,*,image=None,max_output_tokens=1024):
        formatted=self.apply_chat_template(self.processor,self.config,prompt,num_images=1 if image else 0)
        exact_tokens=len(self.processor.tokenizer.encode(formatted))
        if exact_tokens>self.max_context_tokens: raise ContextLimitExceeded("exact token limit exceeded")
        pieces=[]; result=None
        with self.lock:
            for item in self.stream_generate(self.model,self.processor,formatted,image=image,max_tokens=max_output_tokens,temperature=0,enable_thinking=False,verbose=False):
                pieces.append(item.text); result=item
        text="".join(pieces).strip()
        return {"status":"completed" if result and result.finish_reason=="stop" else "incomplete","output_text":text,"output":[{"content":[{"type":"output_text","text":text}]}],"usage":{"input_tokens":result.prompt_tokens if result else exact_tokens,"output_tokens":result.generation_tokens if result else 0},"max_output_tokens":max_output_tokens,"incomplete_details":None if result and result.finish_reason=="stop" else {"reason":result.finish_reason if result else "empty"}}

    def text(self,prompt,max_output_tokens): return self._generate(prompt,max_output_tokens=max_output_tokens)
    def vision(self,image_ref,prompt,max_output_tokens):
        path=Path(image_ref).resolve(strict=True)
        if self.spool_root not in path.parents or not path.is_file() or path.stat().st_size>20*1024**2: raise PermissionError("invalid private image reference")
        return self._generate(prompt,image=str(path),max_output_tokens=max_output_tokens)


def handler_for(engine):
    class Handler(BaseHTTPRequestHandler):
        server_version="LocalQwen38/0.1"
        def log_message(self,format,*args): return
        def _send(self,status,payload):
            body=json.dumps(payload,ensure_ascii=False).encode(); self.send_response(status); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            if self.path=="/health": self._send(200,{"status":"healthy","model":MODEL_ID,"max_context_tokens":engine.max_context_tokens}); return
            self._send(404,{"error":"not_found"})
        def do_POST(self):
            if self.client_address[0] not in {"127.0.0.1","::1"}: self._send(403,{"error":"localhost_only"}); return
            try:
                length=int(self.headers.get("Content-Length","0"))
                if length<=0 or length>24*1024**2: raise ValueError("invalid request size")
                payload=json.loads(self.rfile.read(length)); model=payload.get("model")
                if model!=MODEL_ID: raise ValueError("model denied")
                prompt=payload.get("input"); limit=int(payload.get("max_output_tokens",1024))
                if not isinstance(prompt,str) or not 1<=limit<=4096: raise ValueError("invalid request")
                if self.path=="/v1/responses": result=engine.text(prompt,limit)
                elif self.path=="/v1/vision": result=engine.vision(payload.get("image_ref"),prompt,min(limit,2048))
                else: self._send(404,{"error":"not_found"}); return
                self._send(200,result)
            except ContextLimitExceeded: self._send(413,{"error":"context_limit_exceeded"})
            except PermissionError: self._send(403,{"error":"media_denied"})
            except (ValueError,TypeError,json.JSONDecodeError): self._send(400,{"error":"invalid_request"})
            except Exception as exc: self._send(503,{"error":"runtime_error","category":type(exc).__name__})
    return Handler


def serve(port=DEFAULT_PORT,*,model_path=MODEL_PATH,spool_root=PRIVATE_SPOOL_ROOT):
    engine=Qwen38SidecarEngine(model_path,spool_root=spool_root)
    # MLX/Metal inference is deliberately serialized. A thread-per-request
    # server can move consecutive generations across worker threads and has
    # caused fatal runtime exits; production policy is single concurrency.
    server=HTTPServer(("127.0.0.1",int(port)),handler_for(engine)); server.serve_forever()
