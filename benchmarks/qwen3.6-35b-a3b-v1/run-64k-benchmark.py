#!/usr/bin/env python3
"""One-shot guarded 64K local benchmark. It never changes oMLX configuration."""
import datetime as dt
from html.parser import HTMLParser
import json, os, re, subprocess, threading, time, urllib.request

ROOT = "/Users/jerson/AI/benchmarks/qwen3.6-35b-a3b-v1"
MODEL_PATH = "/Users/jerson/AI/models/mlx-community/Qwen3.6-35B-A3B-4bit"
PID = 58578
RAW = os.path.join(ROOT, "context-64k-raw.json")
SAMPLES = os.path.join(ROOT, "memory-sampler-64k.jsonl")

class TextOnly(HTMLParser):
    def __init__(self): super().__init__(); self.parts=[]; self.skip=0
    def handle_starttag(self, tag, attrs):
        if tag in {"script","style","nav","footer","header"}: self.skip += 1
    def handle_endtag(self, tag):
        if tag in {"script","style","nav","footer","header"} and self.skip: self.skip -= 1
    def handle_data(self, data):
        if not self.skip and data.strip(): self.parts.append(" ".join(data.split()))

def fetch(url):
    req=urllib.request.Request(url, headers={"User-Agent":"local-context-benchmark/1.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        parser=TextOnly(); parser.feed(response.read().decode("utf-8", "ignore"))
    return "\n".join(parser.parts)

def one_sample():
    return json.loads(subprocess.check_output([
        "/usr/bin/python3", os.path.join(ROOT,"memory-sampler.py"), "--pid",str(PID),"--seconds","0"
    ], text=True).strip())

def monitor(stop, state):
    prior=[]
    with open(SAMPLES,"w",encoding="utf-8") as handle:
        while not stop.is_set():
            try:
                point=one_sample(); prior.append(point); prior=prior[-31:]
                # Only explicit danger triggers abort of this HTTP request; no process is stopped.
                if point.get("thermal_state") == "WARNING": state["resource_limit"]="THERMAL_WARNING"
                elif len(prior) >= 3 and all(x.get("memory_pressure_free_percent",100) <= 15 for x in prior[-3:]):
                    state["resource_limit"]="DANGEROUS_MEMORY_PRESSURE"
                elif len(prior) >= 31:
                    swap0=prior[0].get("swap_used_mib"); swap1=prior[-1].get("swap_used_mib")
                    if swap0 is not None and swap1 is not None and swap1-swap0 >= 512 and point.get("memory_pressure_free_percent",100) <= 25:
                        state["resource_limit"]="SUSTAINED_SWAP_GROWTH"
                handle.write(json.dumps(point,ensure_ascii=False)+"\n"); handle.flush()
            except Exception as exc:
                handle.write(json.dumps({"sampler_error":repr(exc)})+"\n"); handle.flush()
            stop.wait(1)
        try: handle.write(json.dumps(one_sample(),ensure_ascii=False)+"\n"); handle.flush()
        except Exception: pass

def main():
    urls=[
      "https://docs.python.org/zh-cn/3/tutorial/introduction.html","https://docs.python.org/zh-cn/3/tutorial/controlflow.html",
      "https://docs.python.org/zh-cn/3/tutorial/datastructures.html","https://docs.python.org/zh-cn/3/tutorial/modules.html",
      "https://docs.python.org/zh-cn/3/tutorial/classes.html","https://docs.python.org/zh-cn/3/tutorial/inputoutput.html",
      "https://docs.python.org/zh-cn/3/tutorial/errors.html","https://docs.python.org/zh-cn/3/library/asyncio-task.html",
      "https://docs.python.org/zh-cn/3/library/argparse.html","https://docs.python.org/zh-cn/3/library/json.html",
      "https://docs.python.org/zh-cn/3/library/pathlib.html","https://docs.python.org/zh-cn/3/library/logging.html",
      "https://docs.python.org/zh-cn/3/library/sqlite3.html","https://docs.python.org/zh-cn/3/library/dataclasses.html",
      "https://docs.python.org/zh-cn/3/library/typing.html","https://docs.python.org/zh-cn/3/library/concurrent.futures.html",
      "https://docs.python.org/zh-cn/3/library/multiprocessing.html","https://docs.python.org/zh-cn/3/library/subprocess.html",
      "https://docs.python.org/zh-cn/3/library/re.html","https://docs.python.org/zh-cn/3/library/unittest.html"
    ]
    source="\n\n".join(fetch(url) for url in urls)
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained(MODEL_PATH,local_files_only=True,trust_remote_code=True)
    ids=tokenizer.encode(source,add_special_tokens=False)
    if len(ids)<61500: raise RuntimeError(f"public corpus too short: {len(ids)} tokens")
    source=tokenizer.decode(ids[:61200],skip_special_tokens=True)
    facts=[
      "F1：北斗迁移批次的不可替代校验码为 CEDAR-041。", "F2：文档镜像的签名轮换日固定为每月第 11 日。",
      "F3：灰鲸流水线的最小人工复核人数是 3 人。", "F4：南窗数据集的保留期限是 270 天。",
      "F5：青栎项目的回退版本标识为 ROLLBACK-8。", "F6：云港发布清单必须由值班负责人和审阅负责人共同签署。",
      "F7：银杉审计的告警阈值为连续 4 次失败。", "F8：赤道归档包的加密轮换周期为 45 天。",
      "F9：晨星索引的默认排序键是 updated_at 降序。", "F10：远帆验收报告的最终编号为 FINAL-203。",
      "F11：白榆异常队列在连续 9 次失败时升级。", "F12：琥珀归档包的密钥轮换周期为 45 天。",
      "F13：海岬演练记录的确认编号为 TIDELINE-096。", "F14：霁月发布窗口的冻结时刻为周五 18:00。",
      "F15：砂岩审阅队列的自动通过门槛是 88 分。", "F16：远航封存件的追踪代码为 OMEGA-071。"
    ]
    positions=[.05,.10,.15,.20,.25,.30,.35,.40,.60,.65,.70,.75,.80,.85,.90,.95]
    pieces=[]; cursor=0
    for fact,ratio in zip(facts,positions):
        cut=int(len(source)*ratio); pieces.extend([source[cursor:cut],"\n\n【64K 基准事实】"+fact+"\n\n"]); cursor=cut
    pieces.append(source[cursor:]); context="".join(pieces)
    prompt="""以下为公开中文技术文档、代码相关说明与嵌入的 16 项基准事实。仅以此上下文作答，不调用外部知识。\n\n请完成：\n1. 写一份 1000–1500 token 的结构化中文技术综述，覆盖代码组织、数据结构、模块、类型、并发、错误处理、测试与输入输出；避免重复。\n2. 逐项准确复述 F1 至 F16，保留编号、代码、数值、日期、角色与排序方向。\n3. 给出四个跨段问题的结论与简短计算：(Q1) F1 与 F16 的数值后缀相差多少？(Q2) F4 与 F12 的期限相差多少天？(Q3) F7 和 F11 的连续失败阈值相差多少次？(Q4) 同时列出 F6 的两个签署角色、F9 的排序键和方向、以及 F14 的冻结时刻。\n\n上下文开始：\n"""+context
    local_tokens=len(tokenizer.encode(prompt,add_special_tokens=False))
    payload={"model":"Qwen3.6-35B-A3B-4bit","messages":[{"role":"user","content":prompt}],"max_tokens":1500,"temperature":0,"stream":True,"stream_options":{"include_usage":True},"chat_template_kwargs":{"enable_thinking":False}}
    stop=threading.Event(); state={"resource_limit":None}; worker=threading.Thread(target=monitor,args=(stop,state),daemon=True); worker.start()
    start=time.monotonic(); first=None; usage=None; output=[]; error=None
    try:
        req=urllib.request.Request("http://127.0.0.1:8000/v1/chat/completions",data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(req,timeout=1800) as response:
            for raw in response:
                if state["resource_limit"]: break
                line=raw.decode("utf-8","replace").strip()
                if not line.startswith("data: ") or line=="data: [DONE]": continue
                event=json.loads(line[6:])
                if event.get("usage"): usage=event["usage"]
                choices=event.get("choices") or []
                if choices and choices[0].get("delta",{}).get("content"):
                    if first is None: first=time.monotonic()
                    output.append(choices[0]["delta"]["content"])
    except Exception as exc: error=repr(exc)
    finally:
        end=time.monotonic(); stop.set(); worker.join(timeout=10)
    result={"timestamp":dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),"source_urls":urls,"local_prompt_tokens":local_tokens,"usage":usage,"ttft_seconds":round(first-start,3) if first else None,"total_time_seconds":round(end-start,3),"api_error":error,"resource_limit":state["resource_limit"],"output":"".join(output),"output_complete":error is None and state["resource_limit"] is None}
    with open(RAW,"w",encoding="utf-8") as handle: json.dump(result,handle,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k not in {"output","source_urls"}},ensure_ascii=False,indent=2))
if __name__=="__main__": main()
