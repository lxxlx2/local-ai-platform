from local_ai_control.bot.media_wizard import MediaWizardController,MediaWizardStore,WizardStep,wizard_summary
from local_ai_control.bot.ui import media_menu,video_production_menu,source_mode_menu,review_video_menu,materials_menu
from local_ai_control.domain.identity import Role


def labels(markup): return [button.text for row in markup.inline_keyboard for button in row]


def test_existing_media_menu_contains_video_submenu_not_new_first_level_button():
    assert "视频" in labels(media_menu(owner=True)) and "新建视频" not in labels(media_menu(owner=True))
    assert "新建视频" in labels(video_production_menu())


def test_restart_safe_owner_wizard_and_one_question_flow(tmp_path):
    path=tmp_path/"wizard.db"; store=MediaWizardStore(path); controller=MediaWizardController(store,job_root=tmp_path/"jobs")
    session=controller.start(Role.OWNER,"owner"); assert session.step is WizardStep.TASK_NAME
    controller.text(Role.OWNER,"owner","Launch Video"); controller.choice(Role.OWNER,"owner","source_mode","DIRECT_BRIEF")
    store.close(); store=MediaWizardStore(path); controller=MediaWizardController(store,job_root=tmp_path/"jobs")
    controller.text(Role.OWNER,"owner","Explain the local AI platform")
    controller.choice(Role.OWNER,"owner","execution_mode","AUTO"); controller.choice(Role.OWNER,"owner","language","en")
    controller.choice(Role.OWNER,"owner","voice","en-male-25-default")
    session=controller.choice(Role.OWNER,"owner","completion_mode","AUTO_COMPLETE")
    visible=wizard_summary(session)
    assert "Launch Video" in visible and "/Users/" not in visible and "{" not in visible
    created=controller.confirm(Role.OWNER,"owner")
    assert created.step is WizardStep.CREATED and (tmp_path/"jobs"/created.values["job_ref"]/'job.json').is_file()


def test_public_cannot_start_or_advance_owner_wizard(tmp_path):
    controller=MediaWizardController(MediaWizardStore(tmp_path/"wizard.db"),job_root=tmp_path/"jobs")
    try: controller.start(Role.PUBLIC,"public")
    except PermissionError: pass
    else: raise AssertionError("public wizard must be denied")


def test_review_actions_and_callback_payloads_are_bounded():
    assert labels(review_video_menu())==["通过并发布","重新生成","修改文稿","取消"]
    for markup in (source_mode_menu(),video_production_menu(),review_video_menu()):
        for row in markup.inline_keyboard:
            for button in row:
                assert len(button.callback_data.encode())<=64 and "/" not in button.callback_data


def test_multiple_links_wait_until_owner_finishes(tmp_path):
    store=MediaWizardStore(tmp_path/"wizard.db")
    controller=MediaWizardController(
        store,
        job_root=tmp_path/"jobs",
        staging_root=tmp_path/"staging",
    )

    controller.start(Role.OWNER,"owner")
    controller.text(Role.OWNER,"owner","URL Video")
    controller.choice(Role.OWNER,"owner","source_mode","LINKS")

    first=controller.text(
        Role.OWNER,
        "owner",
        "https://example.com/requirements",
    )
    assert first.step is WizardStep.MATERIALS

    second=controller.text(
        Role.OWNER,
        "owner",
        "https://example.com/rules",
    )
    assert second.step is WizardStep.MATERIALS
    assert len(second.values["source_urls"]) == 2

    finished=controller.finish_materials(Role.OWNER,"owner")
    assert finished.step is WizardStep.EXECUTION_MODE


def test_multiple_uploads_are_staged_then_copied_to_media_job(tmp_path):
    store=MediaWizardStore(tmp_path/"wizard.db")
    controller=MediaWizardController(
        store,
        job_root=tmp_path/"jobs",
        staging_root=tmp_path/"staging",
    )

    controller.start(Role.OWNER,"owner")
    controller.text(Role.OWNER,"owner","Upload Video")
    controller.choice(Role.OWNER,"owner","source_mode","UPLOADS")

    one=controller.stage_upload_bytes(
        Role.OWNER,
        "owner",
        filename="slides.pptx",
        payload=b"pptx-data",
    )
    assert one.step is WizardStep.MATERIALS

    two=controller.stage_upload_bytes(
        Role.OWNER,
        "owner",
        filename="script.txt",
        payload=b"script-data",
    )
    assert len(two.values["uploads"]) == 2

    controller.finish_materials(Role.OWNER,"owner")
    controller.choice(Role.OWNER,"owner","execution_mode","AUTO")
    controller.choice(Role.OWNER,"owner","language","en")
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

    created=controller.confirm(Role.OWNER,"owner")

    job=tmp_path/"jobs"/created.values["job_ref"]
    staged=list((job/"source").iterdir())

    assert len(staged) == 2
    assert not any((tmp_path/"staging").rglob("*.pptx"))
    assert not any((tmp_path/"staging").rglob("*.txt"))


def test_uploads_and_links_requires_both_before_finish(tmp_path):
    import pytest

    store=MediaWizardStore(tmp_path/"wizard.db")
    controller=MediaWizardController(
        store,
        job_root=tmp_path/"jobs",
        staging_root=tmp_path/"staging",
    )

    controller.start(Role.OWNER,"owner")
    controller.text(Role.OWNER,"owner","Mixed Video")
    controller.choice(
        Role.OWNER,
        "owner",
        "source_mode",
        "UPLOADS_AND_LINKS",
    )

    controller.text(
        Role.OWNER,
        "owner",
        "https://example.com/requirements",
    )

    with pytest.raises(ValueError):
        controller.finish_materials(Role.OWNER,"owner")

    controller.stage_upload_bytes(
        Role.OWNER,
        "owner",
        filename="brief.txt",
        payload=b"material",
    )

    finished=controller.finish_materials(Role.OWNER,"owner")
    assert finished.step is WizardStep.EXECUTION_MODE
