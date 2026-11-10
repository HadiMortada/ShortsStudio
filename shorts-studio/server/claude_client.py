"""Run the official Claude Code CLI without tool allowlists or tool-denial flags.

The runner invokes only the operator's installed Claude CLI. Permissions and
configured tools remain Anthropic's and the machine owner's responsibility.
Never expose the CLI's credential, configuration or raw auth-status JSON.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess


def cli_environment() -> dict[str, str]:
    """Avoid passing runner secrets to an AI child process.

    This is not an OS sandbox; users must run the CLI under an appropriately
    isolated operating-system account when granting agent filesystem tools.
    """
    secret_prefixes = (
        "ANTHROPIC_API", "GOOGLE_", "STUDIO_", "GITHUB_", "GH_", "AWS_",
        "YOUTUBE_", "CLOUDFLARE_", "CF_",
    )
    return {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(secret_prefixes) and name != "PUBLIC_BASE_URL"
    }


def creative_cli_args(prompt: str) -> list[str]:
    """Claude chooses its own available tools under its normal permission rules."""
    return ["claude", "-p", prompt, "--output-format", "text"]


def authentication_status() -> dict[str, object]:
    """Return *only* safe summary fields. No user emails, tokens, or paths."""
    if not shutil.which("claude"):
        return {"installed": False, "authenticated": False, "method": "none"}
    try:
        result = subprocess.run(
            ["claude", "auth", "status"],
            capture_output=True, text=True, check=False, timeout=15,
            env=cli_environment(),
        )
        if result.returncode != 0:
            return {"installed": True, "authenticated": False, "method": "none"}
        status = json.loads(result.stdout or "{}")
        method = status.get("authMethod", "unknown")
        allowed_methods = {
            "claude.ai", "oauth_token", "api_key", "api_key_helper", "third_party",
        }
        return {
            "installed": True,
            "authenticated": bool(status.get("loggedIn", True)),
            "method": method if method in allowed_methods else "unknown",
        }
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {"installed": True, "authenticated": False, "method": "unknown"}
