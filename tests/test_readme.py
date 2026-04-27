import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


def _readme_text() -> str:
    return README.read_text(encoding="utf-8")


class ReadmeDeploymentExampleTests(unittest.TestCase):
    def test_bearer_token_guidance_uses_the_deployment_token_variable(self):
        text = _readme_text()

        self.assertIn("`Authorization: Bearer $TOKEN`", text)
        self.assertIn("TOKEN=replace-with-a-long-random-token", text)
        self.assertIn('-H "Authorization: Bearer $TOKEN"', text)
        self.assertNotIn("Authorization: Bearer ***", text)

    def test_bash_fenced_blocks_are_shell_syntax_valid(self):
        text = _readme_text()
        bash_blocks = re.findall(r"```bash\n(.*?)\n```", text, re.DOTALL)

        self.assertTrue(bash_blocks)
        for block in bash_blocks:
            result = subprocess.run(
                ["bash", "-n"],
                input=block,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
