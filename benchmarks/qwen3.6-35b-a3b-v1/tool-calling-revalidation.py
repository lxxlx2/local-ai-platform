#!/usr/bin/env python3
"""Local-only structured tool-calling revalidation for Qwen/oMLX."""
import json, time, urllib.request
from pathlib import Path

ROOT=Path('/Users/jerson/AI/benchmarks/qwen3.6-35b-a3b-v1')
RAW=ROOT/'tool-calling-revalidation-raw.json'
URL='http://127.0.0.1:8000'
MODEL='Qwen3.6-35B-A3B-4bit'
SCHEMAS={
'get_weather':{'type':'object','properties':{'city':{'type':'string'}},'required':['city']},
'add_numbers':{'type':'object','properties':{'a':{'type':'number'},'b':{'type':'number'}},'required':['a','b']},
'lookup_project_status':{'type':'object','properties':{'project':{'type':'string'}},'required':['project']}}
DESCS={'get_weather':'Return deterministic local weather.','add_numbers':'Add two numbers.','lookup_project_status':'Return deterministic local project status.'}

def post(path,payload):
 r=urllib.request.Request(URL+path,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(r,timeout=180) as x:return json.load(x)
def chat_tools(): return [{'type':'function','function':{'name':n,'description':DESCS[n],'parameters':s}} for n,s in SCHEMAS.items()]
def response_tools(): return [{'type':'function','name':n,'description':DESCS[n],'parameters':s} for n,s in SCHEMAS.items()]
def valid(c,name=None):
 try: a=json.loads(c.get('function',{}).get('arguments','')); return bool(c.get('id')) and (name is None or c['function']['name']==name) and isinstance(a,dict)
 except Exception:return False
def chat(prompt,choice='auto'):
 p={'model':MODEL,'messages':[{'role':'user','content':prompt}],'tools':chat_tools(),'tool_choice':choice,'temperature':0,'max_tokens':300,'chat_template_kwargs':{'enable_thinking':False}}
 return post('/v1/chat/completions',p)
def resp(prompt,choice='auto'):
 p={'model':MODEL,'input':prompt,'tools':response_tools(),'tool_choice':choice,'temperature':0,'max_output_tokens':300,'chat_template_kwargs':{'enable_thinking':False}}
 return post('/v1/responses',p)
def response_call(x): return next((i for i in x.get('output',[]) if i.get('type')=='function_call'),None)
def mock(n,a):
 if n=='get_weather':return '31C, partly cloudy' if a.get('city')=='Bangkok' else '20C, test result'
 if n=='add_numbers':return str(a['a']+a['b'])
 return {'alpha':'ready','beta':'blocked','gamma':'testing'}.get(a.get('project'),'unknown')
def main():
 results=[]; raw={}
 chat_cases=[('C1','What is the weather in Bangkok? Use the available tool if appropriate.','auto','get_weather'),('C2','Find the weather in Bangkok.','required','get_weather'),('C3','Check Bangkok.',{'type':'function','function':{'name':'get_weather'}},'get_weather'),('C4','Add 137 and 285 using the tool.','required','add_numbers'),('C5','Reply with the word hello.','auto',None)]
 for k,q,ch,w in chat_cases:
  try:
   x=chat(q,ch); calls=x['choices'][0]['message'].get('tool_calls') or []; ok=(not calls if w is None else any(valid(c,w) for c in calls)); results.append((k,ok));raw[k]=x
  except Exception as e:results.append((k,False));raw[k]={'error':repr(e)}
 resp_cases=[('R1','What is the weather in Bangkok?','auto','get_weather'),('R2','Find Bangkok weather.','required','get_weather'),('R3','Check Bangkok.',{'type':'function','name':'get_weather'},'get_weather'),('R4','Add 137 and 285 using the tool.','required','add_numbers'),('R5','Reply with the word hello.','auto',None)]
 for k,q,ch,w in resp_cases:
  try:
   x=resp(q,ch); c=response_call(x); ok=(c is None if w is None else bool(c and c.get('call_id') and c.get('name')==w and isinstance(json.loads(c.get('arguments','')),dict))); results.append((k,ok));raw[k]=x
  except Exception as e:results.append((k,False));raw[k]={'error':repr(e)}
 # Reliability: deterministic forced calls, 20 small requests.
 rel=[]
 for i in range(20):
  n=['get_weather','add_numbers','lookup_project_status'][i%3]; project=['alpha','beta','gamma'][i%3]
  q={'get_weather':'Use get_weather for Bangkok.', 'add_numbers':f'Use add_numbers for {i} and {i+1}.','lookup_project_status':f'Use lookup_project_status for {project}.'}[n]
  try:
   x=chat(q,{'type':'function','function':{'name':n}}); c=(x['choices'][0]['message'].get('tool_calls') or [{}])[0]; rel.append(valid(c,n));
  except Exception: rel.append(False)
 raw['reliability']=rel
 # One Responses roundtrip using documented previous_response_id + function_call_output.
 try:
  first=resp('Use the weather tool to check Bangkok, then tell me the result.','required'); c=response_call(first); a=json.loads(c['arguments']); follow={'model':MODEL,'previous_response_id':first['id'],'input':[{'type':'function_call_output','call_id':c['call_id'],'output':mock(c['name'],a)}],'tools':response_tools(),'temperature':0,'max_output_tokens':300,'chat_template_kwargs':{'enable_thinking':False}}; second=post('/v1/responses',follow); text=' '.join(z.get('text','') for i in second.get('output',[]) for z in i.get('content',[]) if z.get('type')=='output_text'); raw['responses_roundtrip']={'first':first,'second':second,'ok':bool(c and '31' in text and 'cloudy' in text.lower())}; results.append(('RESPONSES_ROUNDTRIP',raw['responses_roundtrip']['ok']))
 except Exception as e: raw['responses_roundtrip']={'error':repr(e),'ok':False};results.append(('RESPONSES_ROUNDTRIP',False))
 RAW.write_text(json.dumps(raw,ensure_ascii=False,indent=2))
 print(json.dumps({'tests':results,'reliability_success':sum(rel),'reliability_total':20},ensure_ascii=False))
if __name__=='__main__':main()
