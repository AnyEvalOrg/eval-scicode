"""TrustedRouter wiring for eval-scicode.

Model calls are made by the Inspect *runner*, never from inside the sandbox, so the
sandbox never holds a provider credential. TrustedRouter is the single allowed
inference FQDN for the runner.
"""
from __future__ import annotations

import os

TR_BASE_URL = os.environ.get("TRUSTEDROUTER_BASE_URL", "https://api.trustedrouter.com/v1")
TR_MIN_SDK = "0.7.0"


def model_string(model: str) -> str:
    """Return the Inspect model string routing `model` through TrustedRouter.

    Inspect's OpenAI-compatible provider reads TRUSTEDROUTER_API_KEY and
    TRUSTEDROUTER_BASE_URL for the `trustedrouter` service name.
    """
    return f"openai-api/trustedrouter/{model}"


def require_delegated_key() -> str:
    """The end user's delegated key, obtained via Sign in with TrustedRouter.

    The key is minted into the *user's own workspace*, so inference bills to their
    credits rather than ours. It is full-access to that workspace: TrustedRouter has
    no scope concept, which is stated plainly at the consent hand-off.
    """
    key = os.environ.get("TRUSTEDROUTER_API_KEY")
    if not key:
        raise RuntimeError(
            "TRUSTEDROUTER_API_KEY is not set. Complete the Sign in with TrustedRouter "
            "flow to mint a user-scoped delegated key."
        )
    return key


def spend_cap_note() -> str:
    """Inspect's --cost-limit is inert on this routing path; the cap lives in TR."""
    return (
        "Spend is capped server-side by the delegated key's limit_microdollars and "
        "spend window, set at consent time. Inspect's --cost-limit cannot bind here "
        "because trustedrouter/<model> does not resolve in its pricing table."
    )
