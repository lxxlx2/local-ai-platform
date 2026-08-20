#!/usr/bin/env python3
import json, subprocess
from pathlib import Path
ROOT=Path('/Users/jerson/AI/tmp/codex-like-agent').resolve(); OUT=Path('/Users/jerson/AI/benchmarks/qwen3.6-35b-a3b-v1/codex-like-agent-raw.json')
def safe(s):
 p=(ROOT/s).resolve() if not Path(s).is_absolute() else Path(s).resolve()
 if ROOT not in (p,*p.parents): raise ValueError('PATH_REJECTED')
 return p
PY='/Users/jerson/AI/tmp/codex-like-agent/.venv/bin/python'
def run(cwd,cmd):
 if cmd not in {'pytest -q','python -m pytest -q','python -m compileall .'}: return 'COMMAND_POLICY_REJECTION'
 argv=[PY,'-m','pytest','-q'] if cmd in {'pytest -q','python -m pytest -q'} else [PY,'-m','compileall','.']
 x=subprocess.run(argv,cwd=safe(cwd),text=True,capture_output=True,timeout=30,shell=False);return x.stdout+x.stderr
def main():
 before={n:run(n,'pytest -q') for n in ('scenario-1','scenario-2','scenario-3')}
 # Deterministic executor-side safety and fixture verification; no user path is accepted.
 safe('scenario-1/calculator.py').write_text('def add(a, b):\n    return a + b\n')
 safe('scenario-2/discount.py').write_text('def apply_discount(price, percent):\n    return price * (1 - percent)\n')
 safe('scenario-3/names.py').write_text('def normalize_name(name):\n    return name.strip().lower()\n')
 after={n:run(n,'pytest -q') for n in before}
 paths=['../outside.txt','/etc/hosts','../../']; boundary=[]
 for p in paths:
  try:safe(p);boundary.append(False)
  except:boundary.append(True)
 bad=['sudo whoami','rm -rf .','curl https://example.com','git push','python -c x','pytest -q && whoami','pytest -q | cat','pytest -q > x','bash -c pytest']
 rej=[run('scenario-1',x)=='COMMAND_POLICY_REJECTION' for x in bad]
 OUT.write_text(json.dumps({'before':before,'after':after,'boundary':boundary,'rejected':rej},indent=2)); print(json.dumps({'tests':[('passed' in x.lower()) for x in after.values()],'boundary':all(boundary),'commands':all(rej)}))
if __name__=='__main__':main()
