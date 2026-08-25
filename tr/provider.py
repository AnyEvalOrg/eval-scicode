"""TrustedRouter model provider for Inspect.

Registers `trustedrouter/<model>` as a first-class Inspect provider.

Why this exists rather than using the stock `openai-api/trustedrouter/<model>`:
the stock provider works, but Inspect discards TrustedRouter's routing block, so the
eval log never records WHICH MODEL ACTUALLY SERVED the request. For a platform whose
entire claim is auditability, "we asked for X" and "X answered" are different
statements and the log must carry the second one.

This subclass overrides `on_response` to capture, per generation:
  - the model TrustedRouter actually selected, and the provider and endpoint
  - the true cost in microdollars, from the attested gateway
  - the generation id, so the run can be reconciled against GET /v1/generation

Records are appended as JSONL to $OPENEVALZ_ROUTING_LOG. A file is used rather than
in-memory state because Inspect constructs the ModelAPI internally and the caller
never holds a reference to it.
"""
from __future__ import annotations

import json
import os
from typing import Any

from inspect_ai.model import modelapi
from inspect_ai.model._providers.openai_compatible import OpenAICompatibleAPI

DEFAULT_BASE_URL = "https://api.trustedrouter.com/v1"
ROUTING_LOG_ENV = "OPENEVALZ_ROUTING_LOG"


@modelapi(name="trustedrouter")
class TrustedRouterAPI(OpenAICompatibleAPI):
    """Inspect provider for TrustedRouter, recording what actually served each call."""

    def __init__(
        self,
        model_name: str,
        base_url: str | None = None,
        api_key: str | None = None,
        config: Any = None,
        **model_args: Any,
    ) -> None:
        from inspect_ai.model import GenerateConfig

        super().__init__(
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            config=config if config is not None else GenerateConfig(),
            service="trustedrouter",
            service_base_url=DEFAULT_BASE_URL,
            **model_args,
        )
        self.routing_records: list[dict[str, Any]] = []

    def on_response(self, response: dict[str, Any]) -> None:
        """Capture TrustedRouter's routing and cost metadata for one generation."""
        routing = ((response.get("trustedrouter") or {}).get("routing") or {})
        usage = response.get("usage") or {}
        provider_usage = usage.get("provider_usage") or {}

        record = {
            "requested_model": self.model_name,
            # What actually answered, per the attested gateway's own record of its
            # routing decision. Not the same claim as "the upstream ran these weights".
            "selected_model": routing.get("selected_model")
            or provider_usage.get("selected_model"),
            "selected_provider": routing.get("selected_provider")
            or provider_usage.get("selected_provider"),
            "selected_endpoint": routing.get("selected_endpoint")
            or provider_usage.get("selected_endpoint"),
            "response_model_field": response.get("model"),
            "upstream_attempt_count": routing.get("upstream_attempt_count"),
            "fallback_attempt_count": routing.get("fallback_attempt_count"),
            "cost_microdollars": usage.get("cost_microdollars"),
            "generation_id": provider_usage.get("generation_id"),
            "region": provider_usage.get("region"),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "completion_id": response.get("id"),
        }
        self.routing_records.append(record)

        path = os.environ.get(ROUTING_LOG_ENV)
        if path:
            try:
                with open(path, "a") as f:
                    f.write(json.dumps(record) + "\n")
            except OSError:
                # Never fail a paid run because telemetry could not be written.
                pass

    def total_cost_microdollars(self) -> int:
        return sum(r.get("cost_microdollars") or 0 for r in self.routing_records)

    def served_models(self) -> set[str]:
        """Distinct models that actually answered — more than one means fallback fired."""
        return {r["selected_model"] for r in self.routing_records if r.get("selected_model")}
