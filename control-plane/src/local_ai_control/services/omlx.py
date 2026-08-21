import json
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelReply:
    text: str
    status: str | None
    incomplete_reason: str | None
    output_tokens: int | None
    requested_max_output_tokens: int

    @property
    def complete(self):
        return self.status == "completed" and not self.incomplete_reason


class OmlxProvider:
    def health(self):
        with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5) as response:
            return json.load(response)

    def generate(self, prompt, max_output_tokens=1024):
        body = json.dumps({
            "model": "Qwen3.6-35B-A3B-4bit",
            "input": prompt,
            "max_output_tokens": max_output_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
        }).encode()
        request = urllib.request.Request("http://127.0.0.1:8000/v1/responses", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=90) as response:
            raw = json.load(response)
        incomplete = raw.get("incomplete_details") or {}
        return ModelReply(
            text=extract_text(raw),
            status=raw.get("status"),
            incomplete_reason=incomplete.get("reason") if isinstance(incomplete, dict) else None,
            output_tokens=(raw.get("usage") or {}).get("output_tokens"),
            requested_max_output_tokens=raw.get("max_output_tokens", max_output_tokens),
        )


def extract_text(response):
    if isinstance(response, dict):
        if isinstance(response.get("output_text"), str):
            return response["output_text"]
        for item in response.get("output") or []:
            for content in (item.get("content") or []) if isinstance(item, dict) else []:
                if isinstance(content, dict) and content.get("type") in {"output_text", "text"} and isinstance(content.get("text"), str):
                    return content["text"]
    return ""
