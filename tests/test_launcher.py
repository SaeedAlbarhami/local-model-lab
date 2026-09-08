from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def write_executable(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    path.chmod(0o755)


@dataclass
class LauncherFixture:
    project: Path
    state: Path
    env: dict[str, str]

    @property
    def calls(self) -> list[str]:
        call_log = self.state / "ollama-calls"
        return call_log.read_text().splitlines() if call_log.exists() else []

    @property
    def installed_models(self) -> list[str]:
        return (self.state / "models").read_text().splitlines()

    def set_models(self, *models: str) -> None:
        content = "".join(f"{model}\n" for model in models)
        (self.state / "models").write_text(content)

    def run(self, *arguments: str, **environment: str) -> subprocess.CompletedProcess[str]:
        env = self.env | environment
        return subprocess.run(
            ["/bin/zsh", "-f", str(self.project / "run.sh"), *arguments],
            cwd=self.state,
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )


@pytest.fixture
def launcher(tmp_path: Path) -> LauncherFixture:
    project = tmp_path / "copied project"
    state = tmp_path / "fake state"
    fake_bin = tmp_path / "fake bin"
    project.mkdir()
    state.mkdir()
    fake_bin.mkdir()

    shutil.copy2(PROJECT_ROOT / "run.sh", project / "run.sh")
    shutil.copy2(PROJECT_ROOT / "requirements.txt", project / "requirements.txt")

    write_executable(
        fake_bin / "uname",
        """#!/bin/sh
case "${1:-}" in
  -s) printf '%s\n' Darwin ;;
  -m) printf '%s\n' arm64 ;;
  *) printf '%s\n' Darwin ;;
esac
""",
    )
    write_executable(
        fake_bin / "sw_vers",
        """#!/bin/sh
[ "${1:-}" = "-productVersion" ] && printf '%s\n' 14.7
""",
    )
    write_executable(
        fake_bin / "sysctl",
        """#!/bin/sh
case "$*" in
  *hw.memsize*) printf '%s\n' 68719476736 ;;
  *) printf '%s\n' 0 ;;
esac
""",
    )
    write_executable(
        fake_bin / "curl",
        """#!/bin/sh
printf '%s\n' "$*" >>"$TEST_STATE_DIR/curl-calls"
exit 0
""",
    )
    write_executable(fake_bin / "unzip", "#!/bin/sh\nexit 0\n")

    fake_ollama = fake_bin / "ollama"
    write_executable(
        fake_ollama,
        """#!/bin/sh
set -eu
command_name="${1:-}"
model="${2:-}"
printf '%s\t%s\n' "$command_name" "$model" >>"$TEST_STATE_DIR/ollama-calls"
case "$command_name" in
  --version)
    printf '%s\n' 'ollama version is test'
    ;;
  show)
    grep -Fqx -- "$model" "$TEST_STATE_DIR/models"
    ;;
  pull)
    printf '%s\n' "$model" >>"$TEST_STATE_DIR/models"
    ;;
  serve)
    exit 99
    ;;
  *)
    exit 98
    ;;
esac
""",
    )

    runtime_bin = project / ".runtime" / "bin"
    fake_uv = runtime_bin / "uv"
    write_executable(
        fake_uv,
        """#!/bin/sh
printf '%s\n' "$*" >>"$TEST_STATE_DIR/uv-calls"
case "${1:-}" in
  --version) printf '%s\n' 'uv test' ;;
  pip) exit 0 ;;
  *) exit 97 ;;
esac
""",
    )

    venv_python = project / ".venv" / "bin" / "python"
    write_executable(
        venv_python,
        """#!/bin/sh
printf '%s\n' "$*" >>"$TEST_STATE_DIR/python-calls"
[ "${1:-}" = "-c" ] && exit 0
exit 96
""",
    )
    marker = "\n".join(
        (
            f"project={project.resolve()}",
            "os=Darwin",
            "arch=arm64",
            "python=3.12",
            f"python_install_dir={project.resolve() / '.runtime' / 'python'}",
        )
    )
    (project / ".venv" / ".local-model-lab-runtime").write_text(f"{marker}\n")
    (state / "models").write_text("")

    env = os.environ.copy()
    for key in tuple(env):
        if key.startswith("LOCAL_MODEL_LAB_") or key.startswith("OLLAMA_") or key.startswith("UV_"):
            env.pop(key)
    env.update(
        {
            "PATH": f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin",
            "OLLAMA_BIN": str(fake_ollama),
            "TEST_STATE_DIR": str(state),
        }
    )
    return LauncherFixture(project=project, state=state, env=env)


def test_setup_pulls_only_the_exact_missing_default_model_and_rerun_is_idempotent(
    launcher: LauncherFixture,
) -> None:
    launcher.set_models("llama3.1:8b")

    first = launcher.run("--setup-only")

    assert first.returncode == 0, first.stdout + first.stderr
    assert [call for call in launcher.calls if call.startswith("show\t")] == [
        "show\tllama3.1:8b",
        "show\tllama3.3:70b",
    ]
    assert [call for call in launcher.calls if call.startswith("pull\t")] == [
        "pull\tllama3.3:70b"
    ]
    assert launcher.installed_models == ["llama3.1:8b", "llama3.3:70b"]
    assert "Model already installed: llama3.1:8b" in first.stdout
    assert "Downloading model llama3.3:70b" in first.stdout

    second = launcher.run("--setup-only")

    assert second.returncode == 0, second.stdout + second.stderr
    assert [call for call in launcher.calls if call.startswith("pull\t")] == [
        "pull\tllama3.3:70b"
    ]
    assert second.stdout.count("Model already installed:") == 2
    python_calls = (launcher.state / "python-calls").read_text().splitlines()
    assert all("uvicorn" not in call for call in python_calls)


def test_similarly_prefixed_tag_does_not_count_as_the_exact_model(
    launcher: LauncherFixture,
) -> None:
    launcher.set_models("llama3.1:8b-extra", "llama3.3:70b")

    result = launcher.run("--setup-only")

    assert result.returncode == 0, result.stdout + result.stderr
    assert [call for call in launcher.calls if call.startswith("pull\t")] == [
        "pull\tllama3.1:8b"
    ]


def test_model_override_and_skip_models_never_show_or_pull(
    launcher: LauncherFixture,
) -> None:
    launcher.set_models()

    result = launcher.run(
        "--setup-only",
        "--skip-models",
        LOCAL_MODEL_LAB_MODELS="small:first,small:second",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Skipping model downloads." in result.stdout
    assert not [
        call
        for call in launcher.calls
        if call.startswith("show\t") or call.startswith("pull\t")
    ]
