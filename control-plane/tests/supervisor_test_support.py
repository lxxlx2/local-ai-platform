from local_ai_control.services.supervisor_contracts import CandidateIdentity, CandidateIdentityProvider


class TestCandidateIdentityProvider(CandidateIdentityProvider):
    """Test-only Git identity provider; production always uses the real clean-worktree probe."""

    __test__ = False

    def worktree_is_clean(self) -> bool:
        return True

    def unowned_write_root_paths(self, write_roots=()):
        return ()

    def build_review_patch(self, identity):
        return "diff --git a/control-plane/test.py b/control-plane/test.py\n"

    def snapshot(self, base_commit_sha=None):
        current = super().snapshot(base_commit_sha)
        return CandidateIdentity(
            current.candidate_ref_type, current.candidate_commit_sha, current.candidate_tree_sha,
            current.base_commit_sha, current.candidate_diff_sha256, current.candidate_created_at,
            (
                "control-plane/tests/test_workflow_supervisor.py",
                "control-plane/src/local_ai_control/services/supervisor_contracts.py",
                "control-plane/src/local_ai_control/services/supervisor_round2_review.py",
                "control-plane/src/local_ai_control/services/supervisor_workflow.py",
            ), (),
        )
