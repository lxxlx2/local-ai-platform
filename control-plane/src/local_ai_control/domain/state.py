from enum import StrEnum

class TaskState(StrEnum):
 DRAFT='DRAFT'; READY='READY'; QUEUED='QUEUED'; RUNNING='RUNNING'; WAITING_APPROVAL='WAITING_APPROVAL'; REVISION_REQUESTED='REVISION_REQUESTED'; APPROVED='APPROVED'; REJECTED='REJECTED'; COMPLETED='COMPLETED'; FAILED='FAILED'; CANCELLED='CANCELLED'; PAUSED='PAUSED'
ALLOWED={TaskState.DRAFT:{TaskState.READY,TaskState.CANCELLED},TaskState.READY:{TaskState.QUEUED,TaskState.CANCELLED},TaskState.QUEUED:{TaskState.RUNNING,TaskState.PAUSED,TaskState.CANCELLED},TaskState.RUNNING:{TaskState.WAITING_APPROVAL,TaskState.COMPLETED,TaskState.FAILED,TaskState.PAUSED},TaskState.WAITING_APPROVAL:{TaskState.APPROVED,TaskState.REJECTED,TaskState.REVISION_REQUESTED},TaskState.REVISION_REQUESTED:{TaskState.QUEUED,TaskState.CANCELLED},TaskState.APPROVED:{TaskState.COMPLETED},TaskState.PAUSED:{TaskState.QUEUED,TaskState.CANCELLED}}
def transition(old,new):
 old,new=TaskState(old),TaskState(new)
 if new not in ALLOWED.get(old,set()): raise ValueError(f'illegal transition {old}->{new}')
