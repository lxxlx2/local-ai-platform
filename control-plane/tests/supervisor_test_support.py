from local_ai_control.services.supervisor_contracts import CandidateIdentityProvider


class TestCandidateIdentityProvider(CandidateIdentityProvider):
    """Test-only Git identity provider; production always uses the real clean-worktree probe."""

    __test__ = False

    def worktree_is_clean(self) -> bool:
        return True
