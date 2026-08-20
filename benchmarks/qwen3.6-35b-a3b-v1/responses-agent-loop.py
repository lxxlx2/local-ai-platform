#!/usr/bin/env python3
import json,subprocess,importlib.util
from pathlib import Path
ROOT=Path('/Users/jerson/AI/tmp/codex-like-agent').resolve(); PY=str(ROOT/'.venv/bin/python'); OUT=Path('/Users/jerson/AI/benchmarks/qwen3.6-35b-a3b-v1/responses-agent-loop-raw.json')
spec=importlib.util.spec_from_file_location('t',OUT.with_name('tool-calling-revalidation.py'));t=importlib.util.module_from_spec(spec);spec.loader.exec_module(t)
TOOLS=[{'type':'function','name':n,'description':n,'parameters':{'type':'object','properties':p,'required':list(p)}} for n,p in {'list_files':{'path':{'type':'string'}},'read_file':{'path':{'type':'string'}},'write_file':{'path':{'type':'string'},'content':{'type':'string'}},'run_command':{'command':{'type':'string'},'cwd':{'type':'string'}}}.items()]
def path(s,scenario):
 p=(ROOT/s).resolve()
 if scenario not in p.parts or ROOT not in (p,*p.parents) or '.venv' in p.parts:raise ValueError('PATH_REJECTED')
 return p
def exec_tool(n,a,scenario):
 try:
  if n=='list_files':return '\n'.join(x.name for x in path(a['path'],scenario).iterdir() if x.is_file())
  if n=='read_file':return path(a['path'],scenario).read_text()[:100000]
  if n=='write_file':
   p=path(a['path'],scenario)
   if p.name.startswith('test_'):return 'PATH_REJECTED'
   p.write_text(a['content']);return 'WRITE_OK'
  if n=='run_command':
   if a['command']!='python -m pytest -q' or Path(a['cwd']).as_posix().strip('/')!=scenario:return 'COMMAND_REJECTED'
   x=subprocess.run([PY,'-m','pytest','-q'],cwd=path(scenario,scenario),text=True,capture_output=True,timeout=30,shell=False);return x.stdout+x.stderr
  return 'UNKNOWN_TOOL'
 except Exception as e:return str(e)
def go(scenario,task):
 x=t.post('/v1/responses',{'model':t.MODEL,'input':task,'tools':TOOLS,'tool_choice':'required','max_output_tokens':400,'temperature':0,'chat_template_kwargs':{'enable_thinking':False}});trace=[];final=''
 for _ in range(20):
  calls=[i for i in x.get('output',[]) if i.get('type')=='function_call']
  if not calls:
   final=' '.join(z.get('text','') for i in x.get('output',[]) for z in i.get('content',[]) if z.get('type')=='output_text');break
  outs=[]
  for c in calls:
   try:a=json.loads(c['arguments']);res=exec_tool(c['name'],a,scenario);trace.append({'name':c['name'],'args':a,'result':res[-1000:]});outs.append({'type':'function_call_output','call_id':c['call_id'],'output':res})
   except Exception as e: outs.append({'type':'function_call_output','call_id':c.get('call_id',''), 'output':'ARGUMENT_REJECTED'});trace.append({'error':str(e)})
  x=t.post('/v1/responses',{'model':t.MODEL,'previous_response_id':x['id'],'input':outs,'tools':TOOLS,'max_output_tokens':400,'temperature':0,'chat_template_kwargs':{'enable_thinking':False}})
 final_test=exec_tool('run_command',{'command':'python -m pytest -q','cwd':scenario},scenario) if any(z['name']=='run_command' for z in trace) else 'NOT_RUN_BY_MODEL'
 return {'trace':trace,'final':final,'final_test':final_test,'loop':len(trace)>=20}
def main():
 tasks={'scenario-1':'The tests in this small Python project are failing. Inspect the project, identify the bug, make the minimum safe fix, run the tests, and report the final result.','scenario-2':'The tests in this Python project are failing. Inspect the relevant files, determine the root cause, apply the minimum correct fix, run the tests, and report the result.','scenario-3':'The tests in this Python project are failing. Inspect, fix, run tests, use remaining failures to continue if necessary, then report.'}
 data={k:go(k,v) for k,v in tasks.items()};OUT.write_text(json.dumps(data,indent=2));print(json.dumps({k:{'steps':len(v['trace']),'pass':'passed' in v['final_test'].lower(),'loop':v['loop']} for k,v in data.items()}))
if __name__=='__main__':main()
