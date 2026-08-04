# JobPilot pilot — cloud only

**Local / on-device models are disabled for normal pilot work.** The pilot
uses cloud Codex. JobPilot retains an explicit `JOBPILOT_USE_OLLAMA=1`
emergency fallback, but it is off by default and is not the recommended path.

## What you use

| Tool | Role |
|------|------|
| `jobpilot` / `jp-pilot` | Cloud Codex pilot for the job hunt |
| Claude / ChatGPT desktop | General coding and chat |
| `GEMINI_API_KEY` | Optional in-app JobPilot LLM (tailoring, etc.) |

## Commands

```bash
jobpilot                  # boot pack + cloud Codex
jobpilot --check          # health only
jobpilot jobs             # open roles
jobpilot note "…"         # diary line
jobpilot diary            # read diary
```

`jobpilot --local`, `--ollama`, and `--opencode` exit with a short message.
There is no local agent to start in the normal workflow.

## App-safe rule (still true)

Never put a local `model=` into `~/.codex/config.toml`. Base Codex stays on
OpenAI cloud so the Codex app does not popup or fight the CLI.

## History

Previous default local stack (retired): Ollama + `codex-prime` / Gemma 4 26B
MLX, `~/.codex/ollama-*.toml`, Continue Ollama models, LM Studio, oMLX.
