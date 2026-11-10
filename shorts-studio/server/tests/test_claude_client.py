import unittest
from unittest.mock import patch, Mock

from claude_client import creative_cli_args, cli_environment, authentication_status


class ClaudeClientTests(unittest.TestCase):
    def test_creative_command_uses_normal_claude_permissions(self):
        args = creative_cli_args("Create a memorable video")
        self.assertEqual(args[:3], ["claude", "-p", "Create a memorable video"])
        for option in ("--tools", "--disallowedTools", "--allowedTools",
                       "--dangerously-skip-permissions", "--permission-mode"):
            self.assertNotIn(option, args)

    def test_runner_secrets_are_not_inherited(self):
        with patch.dict("os.environ", {
            "GOOGLE_CLIENT_SECRETS": "/secure/private.json",
            "STUDIO_TOKEN": "secret",
            "ANTHROPIC_API_KEY": "secret",
            "HOME": "/home/runner",
        }, clear=True):
            env = cli_environment()
        self.assertEqual(env, {"HOME": "/home/runner"})

    @patch("claude_client.shutil.which", return_value="/usr/bin/claude")
    @patch("claude_client.subprocess.run")
    def test_auth_status_never_returns_credentials(self, runner, _which):
        runner.return_value = Mock(returncode=0, stdout='{"loggedIn":true,"authMethod":"claude.ai","email":"private@example.com","configDirectory":"/home/private"}')
        status = authentication_status()
        self.assertEqual(status, {"installed": True, "authenticated": True, "method": "claude.ai"})
        self.assertNotIn("email", status)
        self.assertNotIn("configDirectory", status)

    @patch("claude_client.shutil.which", return_value=None)
    def test_missing_cli(self, _which):
        self.assertFalse(authentication_status()["authenticated"])


if __name__ == "__main__":
    unittest.main()
