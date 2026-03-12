"""Tests for the Playwright executor error-detection logic."""

from unittest.mock import patch, MagicMock

from browser_agent.playwright_executor import PlaywrightExecutor


def _fake_proc(stdout: str, stderr: str = "", returncode: int = 0):
    """Create a mock CompletedProcess."""
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    return proc


class TestStdoutErrorDetection:
    """Verify that '### Error' in stdout is normalised to a real failure."""

    @patch("browser_agent.playwright_executor.subprocess.run")
    def test_error_in_stdout_flips_returncode(self, mock_run):
        mock_run.return_value = _fake_proc(
            stdout=(
                "### Ran Playwright code\n"
                "```js\nawait page.getByRole('button').click();\n```\n"
                "### Error\n"
                "TimeoutError: locator.click: Timeout 5000ms exceeded."
            ),
            returncode=0,
        )
        executor = PlaywrightExecutor()
        result = executor.run("playwright-cli click e135")

        assert result.returncode == 1

    @patch("browser_agent.playwright_executor.subprocess.run")
    def test_error_text_moved_to_stderr(self, mock_run):
        mock_run.return_value = _fake_proc(
            stdout=(
                "### Ran Playwright code\n```js\ncode;\n```\n"
                "### Error\n"
                "Error: locator.selectOption: Element is not a <select> element"
            ),
            returncode=0,
        )
        executor = PlaywrightExecutor()
        result = executor.run("playwright-cli select e102 1")

        assert "Element is not a <select> element" in result.stderr
        assert "### Error" not in result.stdout

    @patch("browser_agent.playwright_executor.subprocess.run")
    def test_pre_error_stdout_preserved(self, mock_run):
        mock_run.return_value = _fake_proc(
            stdout=(
                "### Ran Playwright code\n```js\ncode;\n```\n"
                "### Error\n"
                "TimeoutError: something failed"
            ),
            returncode=0,
        )
        executor = PlaywrightExecutor()
        result = executor.run("playwright-cli click e1")

        assert "### Ran Playwright code" in result.stdout
        assert "TimeoutError" not in result.stdout

    @patch("browser_agent.playwright_executor.subprocess.run")
    def test_clean_stdout_untouched(self, mock_run):
        """A successful command with no '### Error' stays returncode 0."""
        mock_run.return_value = _fake_proc(
            stdout=(
                "### Ran Playwright code\n```js\ncode;\n```\n"
                "### Page\n- Page URL: https://example.com"
            ),
            returncode=0,
        )
        executor = PlaywrightExecutor()
        result = executor.run("playwright-cli click e1")

        assert result.returncode == 0
        assert result.stderr == ""
        assert "### Page" in result.stdout

    @patch("browser_agent.playwright_executor.subprocess.run")
    def test_real_nonzero_exit_code_unchanged(self, mock_run):
        """If the process already failed with rc!=0, we don't double-process."""
        mock_run.return_value = _fake_proc(
            stdout="",
            stderr="command not found",
            returncode=127,
        )
        executor = PlaywrightExecutor()
        result = executor.run("playwright-cli bad-command")

        assert result.returncode == 127
        assert result.stderr == "command not found"

    @patch("browser_agent.playwright_executor.subprocess.run")
    def test_existing_stderr_preserved_with_extracted_error(self, mock_run):
        """If there's already stderr AND stdout has ### Error, both are merged."""
        mock_run.return_value = _fake_proc(
            stdout="### Error\nTimeout 5000ms exceeded.",
            stderr="some warning",
            returncode=0,
        )
        executor = PlaywrightExecutor()
        result = executor.run("playwright-cli click e1")

        assert result.returncode == 1
        assert "some warning" in result.stderr
        assert "Timeout 5000ms exceeded." in result.stderr

    @patch("browser_agent.playwright_executor.subprocess.run")
    def test_error_only_stdout_gives_empty_stdout(self, mock_run):
        """When stdout is ONLY the error block, stdout should be empty after."""
        mock_run.return_value = _fake_proc(
            stdout="### Error\nError: frame was detached",
            returncode=0,
        )
        executor = PlaywrightExecutor()
        result = executor.run("playwright-cli click e1")

        assert result.returncode == 1
        assert result.stdout == ""
        assert "frame was detached" in result.stderr
