"""Safe provider metadata and switch orchestration. Credentials are aliases only."""
from dataclasses import dataclass
from urllib.parse import urlparse
import re


_SAFE_ID = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_SAFE_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class ProviderProfile:
    provider_id: str; display_name: str; base_url: str; model_id: str; credential_alias: str | None
    local_or_remote: str; data_egress: str; enabled: bool = True; validated: bool = False

    def validate(self):
        if not _SAFE_ID.fullmatch(self.provider_id) or not _SAFE_MODEL_ID.fullmatch(self.model_id):
            raise ValueError("unsafe provider or model id")
        if self.credential_alias and not _SAFE_ID.fullmatch(self.credential_alias):
            raise ValueError("unsafe credential alias")
        parsed = urlparse(self.base_url)
        if "?" in self.base_url or "#" in self.base_url or parsed.username is not None or parsed.password is not None:
            raise ValueError("provider URL must not contain credentials or parameters")
        if self.local_or_remote == "LOCAL":
            if (self.data_egress != "NONE" or parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}):
                raise ValueError("local provider must use a loopback HTTP URL")
        elif (self.local_or_remote != "REMOTE" or self.data_egress != "REMOTE" or parsed.scheme != "https" or not parsed.hostname
              ):
            raise ValueError("remote provider must declare REMOTE egress and HTTPS")
        return self


LOCAL_OMLX = ProviderProfile("local-omlx", "Local oMLX", "http://127.0.0.1:8000/v1", "qwen3.6-35b-a3b-4bit", None, "LOCAL", "NONE", True, True)


class ProviderRegistry:
    def __init__(self, profiles=(LOCAL_OMLX,)):
        self.profiles = {profile.provider_id: profile.validate() for profile in profiles}

    def list_safe(self):
        return tuple(self.profiles.values())


class ProviderControlService:
    def __init__(self, registry=None, active_by_role=None):
        self.registry = registry or ProviderRegistry()
        self.active_by_role = active_by_role or {"FAST": "local-omlx"}
        self.last_known_good = dict(self.active_by_role)

    def preview(self, role: str, provider_id: str):
        profile = self.registry.profiles[provider_id].validate()
        return {"role": role, "provider": profile.provider_id, "data_egress": profile.data_egress, "requires_confirm": True, "eligible": role == "FAST" and provider_id == "local-omlx"}

    def apply(self, role: str, provider_id: str, *, actor_role: str, owner_confirmed: bool, health_check):
        preview = self.preview(role, provider_id)
        if actor_role != "OWNER": return "AUTHORIZATION_DENIED"
        if not owner_confirmed: return "CONFIRM_REQUIRED"
        if not preview["eligible"]: return "CAPABILITY_MISMATCH"
        # Phase 4C.1C intentionally has no provider/config/session mutation.
        # A future approved profile manager can consume this preview separately.
        health_check(self.registry.profiles[provider_id])
        return "DEFERRED_NO_CONFIG_MUTATION"
