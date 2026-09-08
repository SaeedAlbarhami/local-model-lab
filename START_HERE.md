# Start Here

Local Model Lab is a one-command, localhost-only Ollama chat app for Apple Silicon Macs.

## Quick start

1. Copy this folder to a Mac with Apple Silicon (M1 or newer) and macOS 14+.
2. Open Terminal in the folder.
3. Run:

```bash
/bin/zsh run.sh
```

If executable permissions were preserved, `./run.sh` works too.

The first run automatically installs its own Python environment and packages, installs or reuses Ollama, and downloads:

- `llama3.1:8b`
- `llama3.3:70b`

When startup finishes, open <http://127.0.0.1:8000>.

Keep Terminal open while using the app. Press **Control-C** to stop it. Run the same command again to restart; completed downloads are reused and interrupted downloads resume.

## Before you begin

The first run needs internet access and at least **55 GB free disk space**. The 70B model is about **43 GB** and generally needs **64 GB or more unified memory** to run well. On Macs with less memory, select `llama3.1:8b` in the app.

For setup options and troubleshooting, see [README.md](README.md).
