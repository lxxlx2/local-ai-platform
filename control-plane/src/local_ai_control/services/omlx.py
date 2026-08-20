import json,urllib.request
class OmlxProvider:
 def health(self):
  with urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5) as r:return json.load(r)
 def generate(self,prompt):
  body=json.dumps({'model':'Qwen3.6-35B-A3B-4bit','input':prompt,'max_output_tokens':400,'chat_template_kwargs':{'enable_thinking':False}}).encode()
  with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/v1/responses',data=body,headers={'Content-Type':'application/json'}),timeout=60) as r:return json.load(r)
