#!/usr/bin/env python3
import json, urllib.request
from pathlib import Path
import importlib.util
spec=importlib.util.spec_from_file_location('tool_harness',Path(__file__).with_name('tool-calling-revalidation.py'))
t=importlib.util.module_from_spec(spec); spec.loader.exec_module(t)
ROOT=Path('/Users/jerson/AI/benchmarks/qwen3.6-35b-a3b-v1'); RAW=ROOT/'tool-calling-final-raw.json'
def rcall(prompt,name,args):
 first=t.resp(prompt,{'type':'function','name':name}); c=t.response_call(first); ok=bool(c and c['name']==name and json.loads(c['arguments'])==args)
 if not ok:return {'structured':False}
 follow={'model':t.MODEL,'previous_response_id':first['id'],'input':[{'type':'function_call_output','call_id':c['call_id'],'output':t.mock(name,args)}],'tools':t.response_tools(),'temperature':0,'max_output_tokens':200,'chat_template_kwargs':{'enable_thinking':False}}
 second=t.post('/v1/responses',follow); text=' '.join(z.get('text','') for i in second.get('output',[]) for z in i.get('content',[]) if z.get('type')=='output_text')
 return {'structured':True,'arguments':True,'accepted':True,'grounded':str(t.mock(name,args)).split(',')[0] in text or str(t.mock(name,args)) in text,'text':text,'loop':any(i.get('type')=='function_call' for i in second.get('output',[]))}
def sse(path,payload):
 req=urllib.request.Request(t.URL+path,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','Accept':'text/event-stream'}); events=[]
 with urllib.request.urlopen(req,timeout=180) as x:
  et='';
  for raw in x:
   line=raw.decode().strip()
   if line.startswith('event:'):et=line[6:].strip()
   elif line.startswith('data:'):
    try: events.append((et,json.loads(line[5:].strip())))
    except: pass
 return events
def main():
 rounds=[('Use get_weather for Bangkok.','get_weather',{'city':'Bangkok'}),('Add 137 and 285 using add_numbers.','add_numbers',{'a':137,'b':285}),('Check project beta with lookup_project_status.','lookup_project_status',{'project':'beta'}),('Add -27 and 81 using add_numbers.','add_numbers',{'a':-27,'b':81}),('Use get_weather for Tokyo.','get_weather',{'city':'Tokyo'})]
 multi=[rcall(*x) for x in rounds]
 cp={'model':t.MODEL,'messages':[{'role':'user','content':'Use the weather tool to check Bangkok.'}],'tools':t.chat_tools(),'tool_choice':{'type':'function','function':{'name':'get_weather'}},'stream':True,'max_tokens':200,'chat_template_kwargs':{'enable_thinking':False}}
 ce=sse('/v1/chat/completions',cp); chunks=[d for _,d in ce]; calls=[]
 for d in chunks:
  for c in (d.get('choices') or []):
   calls+=c.get('delta',{}).get('tool_calls') or []
 cname=''.join(c.get('function',{}).get('name','') for c in calls); carg=''.join(c.get('function',{}).get('arguments','') for c in calls); chatstream={'events':len(ce),'call_id':bool(calls and calls[0].get('id')),'name':cname=='get_weather','arguments':carg,'json':False}
 try:chatstream['json']=json.loads(carg)=={'city':'Bangkok'}
 except:pass
 rp={'model':t.MODEL,'input':'Use the weather tool to check Bangkok.','tools':t.response_tools(),'tool_choice':{'type':'function','name':'get_weather'},'stream':True,'max_output_tokens':200,'chat_template_kwargs':{'enable_thinking':False}}
 re=sse('/v1/responses',rp); flat=[d for _,d in re]; fc=[]
 for _,d in re:
  if d.get('type') in ('response.output_item.added','response.output_item.done') and d.get('item',{}).get('type')=='function_call':fc.append(d['item'])
  if d.get('type')=='response.function_call_arguments.done':fc.append(d)
 item=next((x for x in fc if x.get('name')=='get_weather'),{}); arg=next((x.get('arguments') for x in fc if x.get('arguments')), '')
 rs={'events':len(re),'call_id':bool(item.get('call_id')),'name':item.get('name')=='get_weather','arguments':arg,'json':False}
 try:rs['json']=json.loads(arg)=={'city':'Bangkok'}
 except:pass
 RAW.write_text(json.dumps({'multiturn':multi,'chat_stream':chatstream,'responses_stream':rs},indent=2))
 print(json.dumps({'multi_success':sum(x.get('structured') and x.get('grounded') and not x.get('loop') for x in multi),'chat_stream':chatstream,'responses_stream':rs}))
if __name__=='__main__':main()
