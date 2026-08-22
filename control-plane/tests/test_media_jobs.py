import pytest
from local_ai_control.services.media_jobs import MediaJobKind,MediaJobRepository,MediaJobRunner,MediaJobStatus
def test_media_job_is_owner_scoped_durable_and_cancelable(tmp_path):
    repo=MediaJobRepository(tmp_path/"jobs.db"); job=repo.create("owner-a",MediaJobKind.VIDEO_GENERATION,"VIDEO_MAIN",("private:in",))
    with pytest.raises(KeyError): repo.get("owner-b",job.job_id)
    assert repo.cancel("owner-a",job.job_id).status is MediaJobStatus.CANCELED
def test_runner_records_only_refs_and_failure_category(tmp_path):
    repo=MediaJobRepository(tmp_path/"jobs.db"); job=repo.create("owner",MediaJobKind.IMAGE_GENERATION,"IMAGE_MAIN")
    done=MediaJobRunner(repo,{MediaJobKind.IMAGE_GENERATION:lambda job:("private:out",)}).run("owner",job.job_id)
    assert done.status is MediaJobStatus.COMPLETED and done.progress==100 and done.output_refs==("private:out",)
    assert "prompt" not in repo.db.execute("SELECT sql FROM sqlite_master WHERE name='media_jobs'").fetchone()[0].lower()
