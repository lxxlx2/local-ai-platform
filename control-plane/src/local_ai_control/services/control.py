import json
import sqlite3
import uuid
from datetime import datetime, timezone

from local_ai_control.domain.state import transition


def now():
 return datetime.now(timezone.utc).isoformat()


DEFAULT_PROJECTS=(('guidengji','📚 归灯记'),('haixiuxian','📚 还修什么仙'),('x_automation','🐦 X 自动化'),('livestream','🎬 直播'),('stickers','🖼 表情包'))
class ControlPlane:
 def __init__(self,path): self.db=sqlite3.connect(path);self.db.row_factory=sqlite3.Row
 def close(self): self.db.close()
 def migrate(self):
  self.db.executescript('''CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,project TEXT,name TEXT,state TEXT,risk INTEGER,model TEXT,context INTEGER,created_at TEXT,updated_at TEXT);CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY,task_id TEXT,state TEXT,version INTEGER,owner_id TEXT,created_at TEXT,updated_at TEXT);CREATE TABLE IF NOT EXISTS audit_events(id TEXT PRIMARY KEY,kind TEXT,payload TEXT,created_at TEXT);CREATE TABLE IF NOT EXISTS quick_actions(id TEXT PRIMARY KEY,key TEXT UNIQUE,display_name_zh TEXT,project TEXT,enabled INTEGER,display_order INTEGER,task_template TEXT,default_model TEXT,default_context INTEGER,risk_level INTEGER,requires_confirmation INTEGER,created_at TEXT,updated_at TEXT);CREATE TABLE IF NOT EXISTS model_registry(key TEXT PRIMARY KEY,display_name TEXT,role TEXT,enabled INTEGER,default_context INTEGER);''')
  self.db.execute("INSERT OR IGNORE INTO model_registry VALUES ('qwen36_fast','Qwen3.6','FAST / 默认本地模型',1,8192)");self.db.commit()
 def audit(self,k,p): self.db.execute('INSERT INTO audit_events VALUES (?,?,?,?)',(str(uuid.uuid4()),k,json.dumps(p),now()));self.db.commit()
 def create_task(self,project,name,risk=1,model='qwen36_fast',context=8192):
  i=str(uuid.uuid4());self.db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)',(i,project,name,'DRAFT',risk,model,context,now(),now()));self.audit('TASK_CREATED',{'task_id':i});return i
 def get_task(self,i): return self.db.execute('SELECT * FROM tasks WHERE id=?',(i,)).fetchone()
 def set_state(self,i,new):
  row=self.get_task(i)
  if not row: raise KeyError('task not found')
  old=row['state'];transition(old,new);self.db.execute('UPDATE tasks SET state=?,updated_at=? WHERE id=?',(new,now(),i));self.audit('TASK_STATE',{'task_id':i,'from':old,'to':new});self.db.commit()
 def list_tasks(self,states=None,limit=8):
  if states:
   marks=','.join('?' for _ in states);return self.db.execute(f'SELECT * FROM tasks WHERE state IN ({marks}) ORDER BY updated_at DESC LIMIT ?',(*states,limit)).fetchall()
  return self.db.execute('SELECT * FROM tasks ORDER BY updated_at DESC LIMIT ?',(limit,)).fetchall()
 def counts(self):
  rows=self.db.execute('SELECT state,COUNT(*) AS n FROM tasks GROUP BY state').fetchall();d={row['state']:row['n'] for row in rows};return {'running':d.get('RUNNING',0),'waiting_approval':d.get('WAITING_APPROVAL',0),'failed':d.get('FAILED',0)}
 def approval(self,task,owner):
  i=str(uuid.uuid4());self.db.execute('INSERT INTO approvals VALUES (?,?,?,?,?,?,?)',(i,task,'WAITING',1,str(owner),now(),now()));self.db.commit();return i
 def decide(self,i,owner,version,decision):
  a=self.db.execute('SELECT * FROM approvals WHERE id=?',(i,)).fetchone()
  if not a or a['owner_id']!=str(owner): raise PermissionError('owner required')
  if a['state']!='WAITING': return 'ALREADY_PROCESSED'
  if a['version']!=version: raise ValueError('stale approval')
  states={'approve':'APPROVED','reject':'REJECTED','revise':'REVISION_REQUESTED'}
  if decision not in states: raise ValueError('invalid decision')
  state=states[decision];self.db.execute('UPDATE approvals SET state=?,version=?,updated_at=? WHERE id=?',(state,version+1,now(),i));self.audit('APPROVAL_'+state,{'approval_id':i});self.db.commit();return state
 def projects(self): return DEFAULT_PROJECTS
 def actions_for(self,project): return self.db.execute('SELECT * FROM quick_actions WHERE project=? AND enabled=1 ORDER BY display_order,display_name_zh',(project,)).fetchall()
