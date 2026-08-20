#!/usr/bin/env python3
"""Thirty-minute, single-concurrency oMLX production-stability exercise.

The harness only sends localhost API requests and reads system telemetry.  It
never changes the server, model, cache, power state, or memory configuration.
"""
import datetime as dt
from html.parser import HTMLParser
import json, os, subprocess, threading, time, urllib.request

ROOT="/Users/jerson/AI/benchmarks/qwen3.6-35b-a3b-v1"
MODEL_PATH="/Users/jerson/AI/models/mlx-community/Qwen3.6-35B-A3B-4bit"
PID=58578
LOG=os.path.join(ROOT,"stability-30min.log")
RAW=os.path.join(ROOT,"stability-30min-requests.json")

class TextOnly(HTMLParser):
    def __init__(self): super().__init__(); self.parts=[]; self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag in {"script","style","nav","footer","header"}: self.skip+=1
    def handle_endtag(self,tag):
        if tag in {"script","style","nav","footer","header"} and self.skip:self.skip-=1
    def handle_data(self,data):
        if not self.skip and data.strip(): self.parts.append(" ".join(data.split()))

def fetch(url):
    request=urllib.request.Request(url,headers={"User-Agent":"local-stability-benchmark/1.0"})
    with urllib.request.urlopen(request,timeout=30) as response:
        parser=TextOnly(); parser.feed(response.read().decode("utf-8","ignore"))
    return "\n".join(parser.parts)

def sample():
    value=subprocess.check_output(["/usr/bin/python3",os.path.join(ROOT,"memory-sampler.py"),"--pid",str(PID),"--seconds","0"],text=True)
    return json.loads(value)

def append_log(lock,record):
    with lock:
        with open(LOG,"a",encoding="utf-8") as handle:
            handle.write(json.dumps(record,ensure_ascii=False)+"\n")

def monitor(start,stop,state,lock):
    marker=0
    history=[]
    while not stop.is_set():
        try:
            point=sample(); elapsed=time.monotonic()-start; history.append(point); history=history[-7:]
            # Explicit stop criteria for this test only; no process is signalled.
            if point.get("thermal_state")=="WARNING": state["resource_limit"]="THERMAL_WARNING"
            elif len(history)>=3 and all(x.get("memory_pressure_free_percent",100)<=15 for x in history[-3:]): state["resource_limit"]="DANGEROUS_MEMORY_PRESSURE"
            elif len(history)>=7:
                a,b=history[0].get("swap_used_mib"),history[-1].get("swap_used_mib")
                if a is not None and b is not None and b-a>=512 and point.get("memory_pressure_free_percent",100)<=25: state["resource_limit"]="SUSTAINED_SWAP_GROWTH"
            while marker<=30 and elapsed>=marker*60:
                append_log(lock,{"record_type":"sample","minute":marker,"telemetry":point})
                marker+=5
        except Exception as exc:
            append_log(lock,{"record_type":"sampler_error","error":repr(exc)})
        stop.wait(5)
    try:
        point=sample(); append_log(lock,{"record_type":"sample","minute":min(30,round((time.monotonic()-start)/60,2)),"telemetry":point,"final":True})
    except Exception: pass

TASKS=[
 ("A_CHINESE_QA","用中文解释给初学者：模块、异常和上下文管理器分别解决什么问题？给出简洁例子。"),
 ("B_LONG_SUMMARY","写一份结构化中文总结，归纳本文档中的数据结构、函数控制流、模块组织与测试实践。"),
 ("C_STRICT_JSON","仅输出合法 JSON：{\"topic\":string,\"risks\":[string],\"actions\":[string]}，基于上下文给出三项代码质量风险和行动。"),
 ("D_TOOL_CALLING","调用 `classify_incident` 工具一次，参数为 severity、component、summary；根据上下文选择合理的值，不要输出工具调用以外的文本。"),
 ("E_PYTHON_DEBUG","诊断 Python 代码 `items=[1,2,3]; for i in range(len(items)): items.pop(i)` 的问题，并给出两种安全修复。"),
 ("F_JAVA_DEBUG","诊断 Java 代码 `if (name == \"admin\")` 的问题，说明原因并给出正确写法与空值处理。"),
 ("G_SQL_ANALYSIS","给出 SQL：找出 orders 表中最近30天每个 customer_id 的订单数和总金额，按总金额降序；说明索引建议。"),
 ("H_GIT_DIFF_REVIEW","模拟审阅 diff：函数将异常捕获后静默返回 null。列出风险、建议的错误处理与测试点。"),
 ("I_CANON_REVIEW","模拟小说 Canon 审核：角色前文在春季离开海港，后文写其冬季当天仍在海港。列出冲突与最小修正方案。"),
 ("J_NOVEL_PLAN","为一章中文小说制定场景规划：目标、冲突、信息揭示、情绪转折和结尾钩子；避免写完整正文。"),
 ("K_X_CONTENT","生成三条中文 X 热点讨论帖草案：每条包含角度、简短正文、风险提示；不声称未验证事实。"),
 ("L_LIVESTREAM_CLASSIFY","将三句模拟直播字幕分类为：产品问题、价格问题或闲聊，并说明各自的推荐主持人回应。"),
 ("M_STICKER_DESIGN","设计一个贴纸角色概念：轮廓、表情、配色、三个动作与适用情境，中文输出。"),
 ("N_STICKER_MATRIX","给出 4×3 的中文贴纸文案矩阵：四种情绪乘三种使用场景，避免重复短句。"),
 ("O_MULTISTEP_PLAN","制定一个五步技术迁移计划，包含前置检查、执行、验证、回滚和复盘；指出每步成功标准。")
]

def context_schedule():
    # 30 slots: 15 at 8K, 8 at 16K, 7 at 32K.
    return ([8,16,8,32,8,16,8,32,8,16] + [8,32,8,16,8,32,8,16,8,32] + [8,16,8,32,8,16,8,32,8,16])

def run_request(request_id,task_type,instruction,level,ids,tokenizer,state):
    source_size={8:7000,16:15000,32:30000}[level]
    offset=(request_id*17311)%(len(ids)-source_size-1)
    corpus=tokenizer.decode(ids[offset:offset+source_size],skip_special_tokens=True)
    own=f"ISOLATION-{request_id:02d}-ORCHID"
    prompt=("以下是公开中文技术资料。它只属于本次独立请求；不要引用其他请求、记忆或未提供内容。"
            f"本次隔离探针为 {own}，不要在回答中复述该探针。\n\n任务：{instruction}\n\n资料：\n{corpus}")
    tools=None
    payload={"model":"Qwen3.6-35B-A3B-4bit","messages":[{"role":"user","content":prompt}],"max_tokens":550,"temperature":0,"stream":True,"stream_options":{"include_usage":True},"chat_template_kwargs":{"enable_thinking":False}}
    if task_type=="D_TOOL_CALLING":
        tools=[{"type":"function","function":{"name":"classify_incident","description":"Classify an engineering incident","parameters":{"type":"object","properties":{"severity":{"type":"string"},"component":{"type":"string"},"summary":{"type":"string"}},"required":["severity","component","summary"]}}}]
        payload.update({"tools":tools,"tool_choice":{"type":"function","function":{"name":"classify_incident"}}})
    start=time.monotonic(); first=None; usage=None; content=[]; calls=[]; finish=None; error=None
    try:
        req=urllib.request.Request("http://127.0.0.1:8000/v1/chat/completions",data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(req,timeout=420) as response:
            for raw in response:
                if state.get("resource_limit"): break
                line=raw.decode("utf-8","replace").strip()
                if not line.startswith("data: ") or line=="data: [DONE]": continue
                event=json.loads(line[6:])
                if event.get("usage"): usage=event["usage"]
                choices=event.get("choices") or []
                if choices:
                    delta=choices[0].get("delta",{})
                    if delta.get("content"):
                        if first is None:first=time.monotonic()
                        content.append(delta["content"])
                    if delta.get("tool_calls"): calls.extend(delta["tool_calls"])
                    if choices[0].get("finish_reason"): finish=choices[0]["finish_reason"]
    except Exception as exc: error=repr(exc)
    end=time.monotonic(); output="".join(content)
    forbidden=[f"ISOLATION-{n:02d}-ORCHID" for n in range(1,31) if n!=request_id]
    json_valid=None
    if task_type=="C_STRICT_JSON":
        try: json.loads(output); json_valid=True
        except Exception: json_valid=False
    tool_valid=None
    if task_type=="D_TOOL_CALLING": tool_valid=bool(calls) and finish in {"tool_calls","stop"}
    status="PASS" if error is None and not state.get("resource_limit") else "ERROR"
    return {"request_id":request_id,"task_type":task_type,"context_level_k":level,"input_tokens":(usage or {}).get("input_tokens"),"output_tokens":(usage or {}).get("output_tokens"),"ttft_seconds":round(first-start,3) if first else None,"prompt_tokens_per_second":(usage or {}).get("prompt_tokens_per_second"),"generation_tokens_per_second":(usage or {}).get("generation_tokens_per_second"),"total_time_seconds":round(end-start,3),"status":status,"json_valid":json_valid,"tool_call_valid":tool_valid,"output_complete":error is None and finish!="length" and not state.get("resource_limit"),"finish_reason":finish,"context_isolation_clean":not any(x in output for x in forbidden),"observed_error":error,"resource_limit":state.get("resource_limit")}

def main():
    urls=["https://docs.python.org/zh-cn/3/tutorial/introduction.html","https://docs.python.org/zh-cn/3/tutorial/controlflow.html","https://docs.python.org/zh-cn/3/tutorial/datastructures.html","https://docs.python.org/zh-cn/3/tutorial/modules.html","https://docs.python.org/zh-cn/3/tutorial/classes.html","https://docs.python.org/zh-cn/3/tutorial/inputoutput.html","https://docs.python.org/zh-cn/3/tutorial/errors.html","https://docs.python.org/zh-cn/3/library/asyncio-task.html","https://docs.python.org/zh-cn/3/library/argparse.html","https://docs.python.org/zh-cn/3/library/json.html","https://docs.python.org/zh-cn/3/library/pathlib.html","https://docs.python.org/zh-cn/3/library/logging.html","https://docs.python.org/zh-cn/3/library/sqlite3.html","https://docs.python.org/zh-cn/3/library/dataclasses.html","https://docs.python.org/zh-cn/3/library/typing.html","https://docs.python.org/zh-cn/3/library/concurrent.futures.html","https://docs.python.org/zh-cn/3/library/multiprocessing.html","https://docs.python.org/zh-cn/3/library/subprocess.html"]
    source="\n\n".join(fetch(url) for url in urls)
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained(MODEL_PATH,local_files_only=True,trust_remote_code=True)
    ids=tokenizer.encode(source,add_special_tokens=False)
    if len(ids)<50000: raise RuntimeError(f"public corpus too short: {len(ids)}")
    open(LOG,"w",encoding="utf-8").close()
    start=time.monotonic(); stop=threading.Event(); state={"resource_limit":None}; lock=threading.Lock()
    observer=threading.Thread(target=monitor,args=(start,stop,state,lock),daemon=True); observer.start()
    records=[]; levels=context_schedule()
    for index,level in enumerate(levels,1):
        planned=start+(index-1)*60
        while time.monotonic()<planned and not state["resource_limit"]: time.sleep(min(1,planned-time.monotonic()))
        if state["resource_limit"]: break
        task_type,instruction=TASKS[(index-1)%len(TASKS)]
        record=run_request(index,task_type,instruction,level,ids,tokenizer,state)
        records.append(record); append_log(lock,{"record_type":"request",**record})
        if state["resource_limit"]: break
    # Hold the loaded model through the complete 30-minute observation window unless a safety stop triggered.
    deadline=start+1800
    while not state["resource_limit"] and time.monotonic()<deadline: time.sleep(min(5,deadline-time.monotonic()))
    stop.set(); observer.join(timeout=15)
    result={"started_at":dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),"duration_seconds":round(time.monotonic()-start,3),"resource_limit":state["resource_limit"],"requests":records,"source_urls":urls}
    with open(RAW,"w",encoding="utf-8") as handle: json.dump(result,handle,ensure_ascii=False,indent=2)
    print(json.dumps({"duration_seconds":result["duration_seconds"],"resource_limit":state["resource_limit"],"requests_completed":len(records)},ensure_ascii=False))
if __name__=="__main__":main()
