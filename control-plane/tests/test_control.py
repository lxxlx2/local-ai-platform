from local_ai_control.services.control import ControlPlane

def test_transitions_and_idempotent_approval(tmp_path):
 c=ControlPlane(tmp_path/'x.db');c.migrate();t=c.create_task('guidengji','检查最近5章',2);c.set_state(t,'READY');c.set_state(t,'QUEUED');c.set_state(t,'RUNNING');c.set_state(t,'WAITING_APPROVAL');a=c.approval(t,7);assert c.decide(a,7,1,'approve')=='APPROVED';assert c.decide(a,7,1,'approve')=='ALREADY_PROCESSED'

def test_registry_counts_and_project_list(tmp_path):
 c=ControlPlane(tmp_path/'x.db');c.migrate();assert dict(c.projects())['guidengji']=='📚 归灯记';assert c.counts()=={'running':0,'waiting_approval':0,'failed':0}
 assert not c.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_registry'").fetchone()
 task=c.create_task('guidengji','预览'); row=c.get_task(task)
 assert row['model']=='MAIN' and row['context']==16384
