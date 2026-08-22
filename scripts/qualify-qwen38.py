#!/usr/bin/env python3
"""One-shot, localhost-safe Qwen3.8 hardware qualification.

Run only from runtime/qwen38-venv after the complete six-shard snapshot exists.
The report stores metrics/statuses, never generated content or private prompts.
"""
from __future__ import annotations
import argparse
import gc
import json
from pathlib import Path
import re
import socket
import subprocess
import time

MODEL=Path("/Users/jerson/AI/models/qwen38-27b-8bit")
REPORT=Path("/Users/jerson/AI/runtime/qualification/qwen38-v0.1.json")

def memory():
    pressure=subprocess.check_output(["memory_pressure","-Q"],text=True)
    available=int(re.search(r"free percentage: (\d+)%",pressure).group(1))
    page_size=int(subprocess.check_output(["sysctl","-n","hw.pagesize"],text=True))
    vm=subprocess.check_output(["vm_stat"],text=True); pages={}
    for line in vm.splitlines()[1:]:
        if ":" in line:
            key,value=line.split(":",1); pages[key]=int(value.strip().rstrip("."))
    reclaimable=pages.get("Pages free",0)+pages.get("Pages inactive",0)+pages.get("Pages speculative",0)
    swap=subprocess.check_output(["sysctl","-n","vm.swapusage"],text=True).strip()
    return {"available_percent":available,"available_gib":reclaimable*page_size/1024**3,"swap":swap}

def require_complete():
    shards=sorted(MODEL.glob("model-*-of-00006.safetensors"))
    incomplete=list(MODEL.rglob("*.incomplete"))
    if len(shards)!=6 or incomplete: raise RuntimeError("MODEL_SNAPSHOT_INCOMPLETE")

def port_8000_free():
    with socket.socket() as sock: return sock.connect_ex(("127.0.0.1",8000))!=0

def context(processor,target):
    roots=(Path("/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/lib/python3.9"),Path("/usr/lib/python3.9"))
    parts=[]
    for root in roots:
        if not root.exists(): continue
        for path in sorted(root.rglob("*.py")):
            try: value=path.read_text(errors="strict")
            except (OSError,UnicodeError): continue
            parts.append(f"\nFILE {path.name}\n{value}")
            text="".join(parts)
            if len(processor.tokenizer.encode(text))>=target: return text
    raise RuntimeError("PUBLIC_STDLIB_CONTEXT_UNAVAILABLE")

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--try-64k",action="store_true"); args=parser.parse_args()
    require_complete()
    if not port_8000_free(): raise RuntimeError("CURRENT_OMLX_IS_RUNNING_REFUSE_SECOND_HEAVY_MODEL")
    start_memory=memory()
    if start_memory["available_percent"]<55 or start_memory["available_gib"]<40: raise RuntimeError("MEMORY_PREFLIGHT_FAILED")
    from mlx_vlm import generate,load
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.utils import load_config
    import mlx.core as mx
    loaded=time.monotonic(); model,processor=load(str(MODEL)); load_seconds=time.monotonic()-loaded
    config=load_config(str(MODEL)); metrics=[]
    def run(name,prompt,*,image=None,max_tokens=128):
        formatted=apply_chat_template(processor,config,prompt,num_images=1 if image else 0)
        started=time.monotonic(); result=generate(model,processor,formatted,image=image,max_tokens=max_tokens,temperature=0,enable_thinking=False,verbose=False)
        metrics.append({"name":name,"ok":bool(result.text.strip()),"finish_reason":result.finish_reason,
                        "prompt_tokens":result.prompt_tokens,"generation_tokens":result.generation_tokens,
                        "prompt_tps":result.prompt_tps,"generation_tps":result.generation_tps,
                        "peak_memory_gib":result.peak_memory,"total_seconds":time.monotonic()-started})
    run("CHINESE","请用两句中文说明本地推理的一个优点和一个限制。")
    run("ENGLISH","In two sentences, explain one benefit and one limitation of local inference.")
    run("JSON",'只输出JSON对象，字段为status和count，值分别为"ok"和2。')
    run("TOOL_SCHEMA",'只输出JSON：{"name":"lookup","arguments":{"query":"mlx"}}')
    from PIL import Image,ImageDraw
    image_path=REPORT.parent/"qwen38-vision-selftest.png"; image_path.parent.mkdir(parents=True,exist_ok=True)
    canvas=Image.new("RGB",(320,180),"white"); draw=ImageDraw.Draw(canvas); draw.rectangle((25,30,135,145),fill="blue"); draw.ellipse((180,35,290,145),fill="red"); canvas.save(image_path)
    run("VISION","描述图中两个主要形状和颜色。",image=str(image_path))
    run("CONTEXT_32K",context(processor,30000)+"\n请用一句话说明这些是何种语言的标准库源码。",max_tokens=64)
    if args.try_64k and memory()["available_percent"]>=55 and memory()["available_gib"]>=40:
        run("CONTEXT_64K",context(processor,60000)+"\n请用一句话说明这些是何种语言的标准库源码。",max_tokens=64)
    end_memory=memory(); del model,processor; gc.collect(); mx.clear_cache(); time.sleep(5); recovered=memory()
    REPORT.write_text(json.dumps({"model":"mlx-community/Qwen3.8-27B-8bit","revision":"815b83c0df8ffd1d1b5244cf75fd6ef14fca9ef9",
                                  "load_seconds":load_seconds,"start_memory":start_memory,"end_memory":end_memory,
                                  "recovered_memory":recovered,"tests":metrics},ensure_ascii=False,indent=2)+"\n")
    image_path.unlink(missing_ok=True); print(REPORT)

if __name__=="__main__": main()
