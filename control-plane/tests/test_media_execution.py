from pathlib import Path

from local_ai_control.bot.media_wizard import (
    MediaWizardController,
    MediaWizardStore,
)
from local_ai_control.domain.identity import Role


def test_confirm_persists_execution_request_and_inputs(tmp_path):
    controller = MediaWizardController(
        MediaWizardStore(tmp_path / "wizard.db"),
        job_root=tmp_path / "jobs",
        staging_root=tmp_path / "staging",
    )

    controller.start(Role.OWNER, "owner")
    controller.text(
        Role.OWNER,
        "owner",
        "Video Demo",
    )

    controller.choice(
        Role.OWNER,
        "owner",
        "source_mode",
        "UPLOADS",
    )

    controller.stage_upload_bytes(
        Role.OWNER,
        "owner",
        filename="script.txt",
        payload=b"## One\nHello",
    )

    controller.finish_materials(
        Role.OWNER,
        "owner",
    )

    controller.choice(
        Role.OWNER,
        "owner",
        "execution_mode",
        "AUTO",
    )

    controller.choice(
        Role.OWNER,
        "owner",
        "language",
        "en",
    )

    controller.choice(
        Role.OWNER,
        "owner",
        "voice",
        "en-male-25-default",
    )

    controller.choice(
        Role.OWNER,
        "owner",
        "completion_mode",
        "AUTO_COMPLETE",
    )

    created = controller.confirm(
        Role.OWNER,
        "owner",
    )

    job_root = (
        tmp_path
        / "jobs"
        / created.values["job_ref"]
    )

    request = (
        job_root
        / "metadata"
        / "request.json"
    ).read_text("utf-8")

    assert '"language": "en"' in request
    assert '"voice": "en-male-25-default"' in request
    assert '"source_mode": "UPLOADS"' in request

    source_files = list(
        (job_root / "source").glob("*.txt")
    )

    assert len(source_files) == 1
