"""The secret scan, exercised against repositories built for the purpose.

Running it over this repository proves it passes today; it does not prove it
would notice anything. So most of these build a throwaway git repository in a
temporary directory, put one specific mistake in it, and check the scan says
so. Nothing here touches the real repository's history or index.

Every key-shaped string below is a synthetic constant with a visible filler
run, present so there is something for the scan to find.
"""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCANNER = REPO / "scripts" / "scan-secrets.sh"

# Assembled rather than written out, so the file that tests the scanner does
# not itself trip it -- which it did, once the file was committed and the scan
# started seeing it. Nothing here may appear as a literal.
FAKE_KEY = "gsk_" + "A" * 24
FAKE_PRIVATE_KEY = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"
MARKER = "secret-scan: synthetic"


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """A git repository containing only a copy of the scanner."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    target = scripts / "scan-secrets.sh"
    target.write_bytes(SCANNER.read_bytes())
    target.chmod(0o755)
    subprocess.run(
        ["git", "add", "-f", "scripts/scan-secrets.sh"], cwd=tmp_path, check=True
    )
    return tmp_path


def scan(repository: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["./scripts/scan-secrets.sh"],
        cwd=repository,
        capture_output=True,
        text=True,
    )


def commit(repository: Path, name: str, content: str) -> None:
    (repository / name).write_text(content)
    subprocess.run(["git", "add", "-f", name], cwd=repository, check=True)


def test_this_repository_is_clean() -> None:
    """The one case that is not hypothetical."""
    result = subprocess.run(
        [str(SCANNER)], cwd=REPO, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_an_empty_repository_passes(repository: Path) -> None:
    """Otherwise the failures below prove only that it fails at everything."""
    assert scan(repository).returncode == 0


def test_a_tracked_environment_file_is_a_finding(repository: Path) -> None:
    """.gitignore covers this, and `git add -f` walks straight past it."""
    commit(repository, ".env", "GROQ_API_KEY=whatever\n")
    result = scan(repository)
    assert result.returncode == 1
    assert ".env" in result.stderr


def test_an_example_file_is_not_a_finding(repository: Path) -> None:
    """It is the opposite of the problem: a template naming the variables
    and carrying none of the values."""
    commit(repository, ".env.example", "GROQ_API_KEY=\n")
    assert scan(repository).returncode == 0


def test_an_example_file_is_still_read(repository: Path) -> None:
    """Exempt from being tracked, not exempt from being scanned -- a key
    pasted into the template is exactly as published as one anywhere else."""
    commit(repository, ".env.example", f"GROQ_API_KEY={FAKE_KEY}\n")
    assert scan(repository).returncode == 1


def test_a_key_shaped_string_is_a_finding(repository: Path) -> None:
    commit(repository, "config.py", f'KEY = "{FAKE_KEY}"\n')
    result = scan(repository)
    assert result.returncode == 1
    assert "config.py" in result.stderr


def test_a_line_marked_synthetic_is_allowed(repository: Path) -> None:
    """A test that proves redaction has to contain something to redact."""
    commit(repository, "test_fake.py", f'KEY = "{FAKE_KEY}"  # {MARKER}\n')
    assert scan(repository).returncode == 0


def test_the_marker_exempts_one_line_and_not_the_file(repository: Path) -> None:
    """The exemption is per line on purpose. A file-wide one would mean the
    next real key pasted into that file is not a finding either."""
    commit(
        repository,
        "test_fake.py",
        f'MARKED = "{FAKE_KEY}"  # {MARKER}\nLEAKED = "{FAKE_KEY}"\n',
    )
    result = scan(repository)
    assert result.returncode == 1
    assert "LEAKED" in result.stderr
    assert "MARKED" not in result.stderr


def test_an_untracked_environment_file_is_not_a_finding(repository: Path) -> None:
    """It is the normal state of a working checkout, and the scan is about
    what a commit would carry."""
    (repository / ".env").write_text(f"GROQ_API_KEY={FAKE_KEY}\n")
    assert scan(repository).returncode == 0


def test_a_private_key_is_a_finding(repository: Path) -> None:
    commit(repository, "id_rsa", f"{FAKE_PRIVATE_KEY}\nx\n")
    assert scan(repository).returncode == 1
