import hashlib
import json
import os
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from local_ai_control.bot.app import send_rendered_output
from local_ai_control.bot.ui import learning_feedback, learning_menu, settings_menu
from local_ai_control.domain.identity import identity_from_telegram
from local_ai_control.services.authorization import AuthorizationDenied, authorize
from local_ai_control.services.output import TelegramOutputRenderer
from local_ai_control.services.storage import ScopedSQLiteRepository
from local_ai_control.services.learning import (
    BASE_MODEL,
    DATASET_NAMESPACES,
    AdapterRegistry,
    AdapterStatus,
    BoundedLocalContentStore,
    BusinessOutcome,
    BusinessOutcomeScorer,
    CandidateStatus,
    DatasetBuildJobSpec,
    DatasetBuilder,
    DatasetSplit,
    EvalJobSpec,
    FeedbackService,
    FeedbackType,
    GoldenEvalHarness,
    LearningImportExport,
    LearningRepository,
    LearningService,
    MLXLoRATrainingProvider,
    PreferencePair,
    PrivacyFilter,
    S3CompatibleContentStore,
    SourceType,
    TrainingJobSpec,
    TrainingFormatSerializer,
    TrainingPriorityService,
    AdapterPromotionJobSpec,
    content_hash,
    retention_dry_run,
)


def engine(tmp_path, quota=2_000_000):
    repository = LearningRepository(tmp_path / "learning.db"); repository.migrate()
    store = BoundedLocalContentStore(tmp_path / "content", max_bytes=quota, retention_days=30)
    service = LearningService(repository, store)
    return repository, store, service


def approved(service, prompt, response, namespace="personal-general", source=None, **kwargs):
    return service.capture_candidate(
        user_scope="OWNER_PRIVATE", namespace=namespace, project_scope="owner",
        source_type=kwargs.pop("source_type", SourceType.MANUAL_IMPORT),
        source_ref=source or content_hash(prompt, response), prompt=prompt, response=response,
        owner_approved=True, quality_labels=kwargs.pop("quality_labels", ("INSTRUCTION_FOLLOWING",)), **kwargs,
    )


def test_schema_and_metrics_are_metadata_only(tmp_path):
    repository, store, service = engine(tmp_path)
    tables = {row[0] for row in repository.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"learning_candidates", "preference_pairs", "datasets", "dataset_examples",
            "dataset_manifests", "adapter_versions", "eval_runs", "eval_results",
            "business_outcomes", "learning_events"} <= tables
    assert repository.metrics()["candidate_count"] == 0
    assert DATASET_NAMESPACES == {"personal-general", "x-content", "novel-editor",
                                  "livestream-content", "stickers-content", "coding-assistant"}
    repository.close()


def test_learn_gold_001_owner_good_creates_approved_sft_candidate(tmp_path):
    repository, store, service = engine(tmp_path)
    candidate, pair = FeedbackService(service).record(
        feedback=FeedbackType.GOOD, prompt="请简洁解释 API", response="API 是软件之间的接口。",
        namespace="personal-general", source_ref="owner-message-1",
        quality_labels=("FACTUAL", "INSTRUCTION_FOLLOWING"),
    )
    assert pair is None and candidate.status is CandidateStatus.APPROVED and candidate.owner_approved
    dataset = DatasetBuilder(repository, store).build("personal-general")
    examples = DatasetBuilder(repository, store).examples(dataset.dataset_id)
    assert len(examples) == 1 and examples[0].format_type.value == "SFT"
    repository.close()


def test_learn_gold_002_owner_correction_creates_preference_pair(tmp_path):
    repository, store, service = engine(tmp_path)
    candidate, pair = FeedbackService(service).record(
        feedback=FeedbackType.BETTER_RESPONSE, prompt="写标题", response="震惊！快看！",
        better_response="用事实说明产品收益。", namespace="x-content", source_ref="owner-message-2",
        quality_labels=("STYLE", "BUSINESS_EFFECTIVENESS"),
    )
    assert candidate.status is CandidateStatus.APPROVED
    assert isinstance(pair, PreferencePair) and pair.owner_confirmed
    dataset = DatasetBuilder(repository, store).build("x-content")
    assert {item.format_type.value for item in DatasetBuilder(repository, store).examples(dataset.dataset_id)} == {"SFT", "PREFERENCE"}
    assert FeedbackService(service).delete_pair(pair.pair_id)
    assert not FeedbackService(service).delete_pair(pair.pair_id)
    repository.close()


def test_learn_gold_003_fake_secret_rejected_before_content_persistence(tmp_path):
    repository, store, service = engine(tmp_path)
    fake = "pass" + "word=example-sensitive-value"
    candidate = service.capture_candidate(
        user_scope="OWNER_PRIVATE", namespace="personal-general", project_scope="owner",
        source_type=SourceType.MANUAL_IMPORT, source_ref="secret-fixture", prompt="配置", response=fake,
    )
    row = repository.db.execute("SELECT * FROM learning_candidates WHERE candidate_id=?", (candidate.candidate_id,)).fetchone()
    assert candidate.status is CandidateStatus.REJECTED
    assert row["rejection_reason"] == "REJECTED_SECRET" and row["prompt_ref"] is None and row["response_ref"] is None
    assert store.used_bytes() == 0 and fake not in repository.path.read_bytes().decode("utf-8", errors="ignore")
    repository.close()


def test_learn_gold_004_public_default_creates_no_candidate(tmp_path):
    repository, store, service = engine(tmp_path)
    result = service.capture_candidate(
        user_scope="PUBLIC_USER", namespace="personal-general", project_scope="public",
        source_type=SourceType.TELEGRAM_OWNER_CHAT, source_ref="public-message",
        prompt="hello", response="world",
    )
    assert result is None and repository.metrics()["candidate_count"] == 0 and store.used_bytes() == 0
    repository.close()


def test_learn_gold_005_non_holdout_cannot_later_become_golden(tmp_path):
    repository, store, service = engine(tmp_path)
    candidate = approved(service, "问题", "答案")
    first = DatasetBuilder(repository, store).build("personal-general")
    assert DatasetBuilder(repository, store).examples(first.dataset_id)[0].split is not DatasetSplit.GOLDEN_HOLDOUT
    with pytest.raises(ValueError, match="Golden Holdout"):
        DatasetBuilder(repository, store).build("personal-general", [candidate.candidate_id])
    repository.close()


def register_adapter(repository, tmp_path, name):
    root = tmp_path / "adapters"; root.mkdir(exist_ok=True)
    artifact = root / f"{name}.safetensors"; artifact.write_bytes(f"synthetic-{name}".encode())
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    registry = AdapterRegistry(repository, root)
    adapter_id = registry.register(name=name, namespace="x-content", base_model_revision="fixture-revision",
                                   dataset_manifest_hash="a" * 64, training_config_hash="b" * 64,
                                   artifact_path=artifact, artifact_hash=digest)
    return registry, adapter_id


def x_scores(value):
    return {key: value for key in ("hook_quality", "clarity", "factual_discipline", "no_invented_source",
                                   "platform_fit", "conciseness", "cta_quality", "safety")}


def test_learn_gold_006_security_regression_denies_adapter_promotion(tmp_path):
    repository, store, service = engine(tmp_path)
    registry, adapter_id = register_adapter(repository, tmp_path, "unsafe")
    base, adapter = x_scores(0.7), x_scores(0.8); adapter["safety"] = 0.6
    comparison = GoldenEvalHarness(repository).compare("x-content", base, adapter, adapter_id)
    assert not comparison.promotion_allowed and not registry.promote(adapter_id, comparison)
    assert registry.list()[0]["status"] == AdapterStatus.EVAL_FAILED.value
    repository.close()


def outcome(candidate_id, quality_pass):
    return BusinessOutcome(str(hash(candidate_id)) + ("p" if quality_pass else "f"), "x-content", candidate_id,
                           content_hash("external"), "2026-08-21T00:00:00+00:00",
                           {"impressions": 100, "clicks": 100, "conversions": 100, "likes": 100,
                            "replies": 100, "reposts": 100}, 100, "USD", "7d", True, "MANUAL", quality_pass)


def test_learn_gold_007_strong_outcome_quality_fail_not_promoted(tmp_path):
    repository, store, service = engine(tmp_path)
    candidate = approved(service, "X 帖子", "安全正文", namespace="x-content")
    assert BusinessOutcomeScorer().record(repository, outcome(candidate.candidate_id, False)) == 0
    assert not service.get_candidate(candidate.candidate_id).business_outcome_validated
    repository.close()


def test_learn_gold_008_strong_outcome_quality_pass_increases_priority(tmp_path):
    repository, store, service = engine(tmp_path)
    candidate = approved(service, "X 帖子", "安全正文", namespace="x-content",
                         quality_labels=("BUSINESS_EFFECTIVENESS",))
    before = TrainingPriorityService(repository).top()[0]["priority_score"]
    assert BusinessOutcomeScorer().record(repository, outcome(candidate.candidate_id, True)) > 0
    after = TrainingPriorityService(repository).top()[0]["priority_score"]
    assert after > before and service.get_candidate(candidate.candidate_id).business_outcome_validated
    repository.close()


def test_learn_gold_009_deleted_candidate_excluded_from_next_dataset(tmp_path):
    repository, store, service = engine(tmp_path)
    candidate = approved(service, "删除问题", "删除答案")
    result = service.delete_candidate(candidate.candidate_id)
    dataset = DatasetBuilder(repository, store).build("personal-general")
    assert result == {"deleted": True, "data_removed_from_future_training": True,
                      "existing_adapter_may_contain_learned_effect": True}
    assert DatasetBuilder(repository, store).examples(dataset.dataset_id) == []
    repository.close()


def test_learn_gold_010_synthetic_only_excluded_by_default(tmp_path):
    repository, store, service = engine(tmp_path)
    candidate = service.capture_candidate(
        user_scope="OWNER_PRIVATE", namespace="x-content", project_scope="fixture",
        source_type=SourceType.SYNTHETIC, source_ref="synthetic-1", prompt="hook", response="body",
        synthetic_flag=True,
    )
    assert candidate.status is CandidateStatus.PENDING
    dataset = DatasetBuilder(repository, store).build("x-content")
    assert dataset.counts[DatasetSplit.TRAIN.value] == 0
    repository.close()


def test_privacy_filter_redacts_identifiers_before_storage(tmp_path):
    repository, store, service = engine(tmp_path)
    candidate = approved(service, "联系 me@example.com", "电话 13800138000")
    assert set(candidate.privacy_labels) == {"EMAIL", "PHONE"}
    assert "example.com" not in candidate.prompt and "13800138000" not in candidate.response
    repository.close()


def test_deterministic_split_dedupe_and_manifest(tmp_path):
    repository, store, service = engine(tmp_path)
    for index in range(20):
        approved(service, f"prompt-{index}", f"response-{index}", source=f"source-{index}")
    approved(service, "prompt-0", "response-0", source="duplicate-source")
    builder = DatasetBuilder(repository, store, fixed_seed=7)
    first = builder.build("personal-general")
    second = builder.build("personal-general")
    first_splits = {item.content_hash: item.split for item in builder.examples(first.dataset_id)}
    second_splits = {item.content_hash: item.split for item in builder.examples(second.dataset_id)}
    assert len(first_splits) == 20 and first_splits == second_splits
    assert first.manifest_hash != second.manifest_hash and sum(first.counts.values()) == 20
    repository.close()


def test_dataset_tables_are_immutable(tmp_path):
    repository, store, service = engine(tmp_path)
    approved(service, "immutable", "answer")
    dataset = DatasetBuilder(repository, store).build("personal-general")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        repository.db.execute("UPDATE datasets SET version=99 WHERE dataset_id=?", (dataset.dataset_id,))
    repository.db.rollback(); repository.close()


def test_adapter_promotion_single_active_and_rollback(tmp_path):
    repository, store, service = engine(tmp_path)
    harness = GoldenEvalHarness(repository)
    registry, first = register_adapter(repository, tmp_path, "first")
    first_eval = harness.compare("x-content", x_scores(0.6), x_scores(0.8), first)
    assert registry.promote(first, first_eval)
    _, second = register_adapter(repository, tmp_path, "second")
    second_eval = harness.compare("x-content", x_scores(0.6), x_scores(0.85), second)
    assert registry.promote(second, second_eval)
    assert sum(item["status"] == "ACTIVE" for item in registry.list()) == 1
    assert registry.rollback(second) == first
    assert next(item for item in registry.list() if item["adapter_id"] == first)["status"] == "ACTIVE"
    with pytest.raises(ValueError, match="not eligible"):
        registry.promote(first, first_eval)
    repository.close()


def test_promotion_requires_bound_passing_eval(tmp_path):
    repository, store, service = engine(tmp_path)
    registry, adapter_id = register_adapter(repository, tmp_path, "forged")
    comparison = GoldenEvalHarness(repository).compare("x-content", x_scores(0.6), x_scores(0.8), None)
    with pytest.raises(PermissionError):
        registry.promote(adapter_id, comparison)
    repository.close()


def test_content_quota_symlink_and_s3_disabled(tmp_path):
    repository, store, service = engine(tmp_path, quota=10)
    store.put("12345")
    with pytest.raises(OSError, match="quota"):
        store.put("678901")
    outside = tmp_path / "outside"; outside.write_text("x")
    symlink = store.root / "aa" / ("a" * 64); symlink.parent.mkdir(); symlink.symlink_to(outside)
    with pytest.raises(PermissionError):
        store.get("sha256:" + "a" * 64)
    with pytest.raises(RuntimeError, match="not configured"):
        S3CompatibleContentStore().put("x")
    repository.close()


def test_safe_import_export_and_path_security(tmp_path):
    repository, store, service = engine(tmp_path)
    transfer = LearningImportExport(service, tmp_path / "runtime")
    source = transfer.import_root / "items.jsonl"
    source.write_text(json.dumps({"prompt": "p", "response": "r", "source_ref": "1", "owner_approved": True}) + "\n" +
                      json.dumps({"prompt": "p", "response": "r", "source_ref": "2", "owner_approved": True}) + "\n")
    assert transfer.import_jsonl(source, "personal-general") == {"created": 1, "rejected": 0, "duplicates": 1}
    dataset = DatasetBuilder(repository, store).build("personal-general")
    export = transfer.export_redacted_jsonl(dataset.dataset_id, transfer.export_root / "dataset.jsonl")
    assert export.is_file() and json.loads(export.read_text().splitlines()[0])["input"] == "p"
    with pytest.raises(PermissionError):
        transfer.import_jsonl(tmp_path / "outside.jsonl", "personal-general")
    symlink = transfer.import_root / "link.jsonl"; symlink.symlink_to(source)
    with pytest.raises(PermissionError):
        transfer.import_jsonl(symlink, "personal-general")
    repository.close()


def test_retention_is_dry_run_and_deletes_nothing(tmp_path):
    repository, store, service = engine(tmp_path)
    candidate = service.capture_candidate(
        user_scope="OWNER_PRIVATE", namespace="personal-general", project_scope="owner",
        source_type=SourceType.MANUAL_IMPORT, source_ref="old", prompt="p", response="r",
    )
    old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    repository.db.execute("UPDATE learning_candidates SET created_at=? WHERE candidate_id=?", (old, candidate.candidate_id)); repository.db.commit()
    report = retention_dry_run(repository)
    assert report["dry_run"] and report["candidate_count"] == 1 and report["deleted"] == 0
    assert service.get_candidate(candidate.candidate_id)
    repository.close()


def test_training_formats_are_valid_utf8_bounded_and_secret_safe():
    serializer = TrainingFormatSerializer()
    chat = serializer.normalized_chat("问题", "回答")
    assert chat["messages"][0] == {"role": "user", "content": "问题"}
    assert json.loads(serializer.sft_jsonl("问题", "回答"))["messages"][1]["role"] == "assistant"
    preference = json.loads(serializer.preference_jsonl("问题", "好答案", "坏答案"))
    assert preference["chosen"] == "好答案" and preference["rejected"] == "坏答案"
    fake = "access_" + "token=" + "a" * 20
    with pytest.raises(ValueError, match="Secret Firewall"):
        serializer.sft_jsonl("配置", fake)
    with pytest.raises(ValueError, match="metadata rejected"):
        serializer.sft_jsonl("问题", "回答", {"nested": {"credential": fake}})


@pytest.mark.parametrize("payload", [
    "seed " + "phrase should not be retained",
    "Authorization: Basic " + "A" * 20,
    "session=" + "b" * 20,
])
def test_warn_or_expanded_secret_patterns_never_persist_content(tmp_path, payload):
    repository, store, service = engine(tmp_path)
    candidate = service.capture_candidate(
        user_scope="OWNER_PRIVATE", namespace="personal-general", project_scope="owner",
        source_type=SourceType.MANUAL_IMPORT, source_ref=content_hash(payload),
        prompt="安全检查", response=payload,
    )
    row = repository.db.execute(
        "SELECT prompt_ref,response_ref,rejection_reason FROM learning_candidates WHERE candidate_id=?",
        (candidate.candidate_id,),
    ).fetchone()
    assert row["prompt_ref"] is None and row["response_ref"] is None
    assert row["rejection_reason"] == "REJECTED_SECRET" and store.used_bytes() == 0
    repository.close()


def test_business_outcome_rejects_metric_injection(tmp_path):
    repository, store, service = engine(tmp_path)
    candidate = approved(service, "X", "正文", namespace="x-content")
    bad = BusinessOutcome("bad", "x-content", candidate.candidate_id, content_hash("external"),
                          "2026-08-21T00:00:00+00:00", {"unknown_metric": 99}, 0, "USD", "7d",
                          True, "MANUAL", True)
    with pytest.raises(ValueError, match="unsupported"):
        BusinessOutcomeScorer().record(repository, bad)
    repository.close()


def test_mlx_probe_config_determinism_and_training_disabled(tmp_path):
    provider = MLXLoRATrainingProvider(training_venv=tmp_path / "missing-venv",
                                       model_path=tmp_path / "model", adapter_root=tmp_path / "runtime/adapters")
    capability = provider.probe()
    assert capability.training_venv_status == "NOT_CONFIGURED" and not capability.estimated_training_ready
    runtime = tmp_path / "runtime"; dataset = runtime / "dataset"; adapter = runtime / "adapters/a"
    dataset.mkdir(parents=True); adapter.parent.mkdir(parents=True)
    import local_ai_control.services.learning as learning
    original = learning.LEARNING_RUNTIME
    learning.LEARNING_RUNTIME = runtime
    try:
        first = provider.build_config(dataset_path=dataset, adapter_path=adapter)
        second = provider.build_config(dataset_path=dataset, adapter_path=adapter)
    finally:
        learning.LEARNING_RUNTIME = original
    assert first.config_hash == second.config_hash and first.base_model == BASE_MODEL
    assert provider.train(first) == {"status": "DISABLED", "reason": "PRODUCTION_TRAINING_DISABLED_BY_DEFAULT",
                                      "config_hash": first.config_hash, "base_model_modified": False}


def test_supervisor_contract_dtos_have_no_branch_import_dependency():
    training = TrainingJobSpec("x-content", "a" * 64, BASE_MODEL, "b" * 64)
    assert training.namespace == DatasetBuildJobSpec("x-content").namespace
    assert EvalJobSpec("x-content", BASE_MODEL, None).adapter_id is None
    assert AdapterPromotionJobSpec("adapter", "eval").expected_eval_run_id == "eval"


def test_owner_learning_navigation_and_public_denial():
    labels = lambda markup: [button.text for row in markup.inline_keyboard for button in row]
    callbacks = lambda markup: [button.callback_data for row in markup.inline_keyboard for button in row]
    assert labels(settings_menu()) == ["学习与训练", "隐私说明", "返回"]
    assert labels(learning_menu()) == ["训练候选", "我的反馈", "数据集", "评估", "Adapter", "隐私设置", "返回"]
    markup = learning_feedback("12345678-1234-1234-1234-123456789012")
    assert labels(markup) == ["加入训练候选", "不满意", "跳过"]
    assert all(len(value.encode()) <= 64 for value in callbacks(markup))
    authorize(identity_from_telegram(1, "1"), "owner:learning")
    with pytest.raises(AuthorizationDenied):
        authorize(identity_from_telegram(2, "1"), "owner:learning")


def test_feedback_source_pair_is_owner_scoped(tmp_path):
    private = ScopedSQLiteRepository(tmp_path / "private.db", "private"); private.migrate()
    owner = identity_from_telegram(1, "1"); public = identity_from_telegram(2, "1")
    session = private.create_session(owner)
    private.add_message(owner, session, "user", "问题")
    answer_id = private.add_message(owner, session, "assistant", "回答")
    prompt, answer = private.message_pair_for_feedback(owner, answer_id)
    assert prompt["content"] == "问题" and answer["content"] == "回答"
    with pytest.raises(AuthorizationDenied):
        private.message_pair_for_feedback(public, answer_id)
    private.close()


def test_feedback_markup_is_attached_only_to_final_transport_chunk():
    import asyncio

    class Message:
        def __init__(self): self.calls = []
        async def answer(self, text, **kwargs): self.calls.append((text, kwargs))

    message = Message(); markup = learning_feedback("12345678-1234-1234-1234-123456789012")
    rendered = asyncio.run(send_rendered_output(message, TelegramOutputRenderer(),
                                                "第一段。" * 1000, markup))
    assert len(message.calls) == len(rendered.chunks) > 1
    assert all("reply_markup" not in kwargs for _, kwargs in message.calls[:-1])
    assert message.calls[-1][1]["reply_markup"] is markup
