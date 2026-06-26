# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Slack bot that runs on AWS Lambda + Amazon Bedrock. Mention `@bot question` and it replies in the thread. The bot's persona is a senior engineer who speaks in casual *gyaru* style — friendly tone, but technically accurate (code/commands are never garbled). It is intentionally a **minimal skeleton** meant to be forked and extended.

## Commands

```bash
# Dev deps (pytest + ruff only — no boto3/slack/strands needed for tests)
pip install -r requirements-dev.txt

python -m pytest tests/                      # all tests
python -m pytest tests/test_persona.py       # one file
python -m pytest tests/test_unit.py::test_build_conversation_caps_history  # one test
ruff check .                                 # lint (line-length 100, py312)
python -m compileall -q slackbot scripts     # syntax-check deployables (what CI runs)
```

CI (`.github/workflows/ci.yml`) runs exactly the last three steps. It needs **no secrets** — see "Why tests need no dependencies" below.

## Two implementations, pick one

The repo ships **two interchangeable handlers** sharing the same `slackbot/core.py`, `persona.py`, and Slack wiring. They differ only in how memory works:

| | `slackbot/app.py` (DIY) | `slackbot/app_strands.py` (Strands) |
|---|---|---|
| Long-term memory | DynamoDB + a self-written extraction prompt (2nd Bedrock call per turn) | AgentCore Memory (auto-extracted, managed) |
| Heavy deps | boto3, slack-bolt | strands-agents, bedrock-agentcore |
| Lambda handler | `slackbot.app.handler` | `slackbot.app_strands.handler` |

Both are deployed by zipping the whole `slackbot/` package. `scripts/create_memory.py` is a one-time setup script for the Strands version only (run as `python -m scripts.create_memory` from repo root); it is **not** deployed.

## Architecture that spans files

**Lazy-listener pattern (Lambda invokes itself).** To satisfy Slack's 3-second ack rule, the handler `ack()`s immediately, then re-invokes the Lambda asynchronously to do the slow Bedrock call. This means the Lambda execution role **must have `lambda:InvokeFunction` on itself** or replies never appear. `is_slack_retry()` drops Slack's timeout re-sends to prevent double replies.

**Pure logic is isolated from I/O so tests need no heavy deps.** `slackbot/core.py`, `namespaces.py`, and `persona.py` import only stdlib. All external I/O (Slack/Bedrock/DynamoDB) is injected into `core.prepare_reply` / `update_long_term_memory` as **callables** from the `app*.py` wiring. The `app*.py` files are the *only* place that imports boto3/slack/strands and reads `os.environ` at module load. Therefore:
- Tests import only `slackbot.core` / `namespaces` / `persona` / `scripts.create_memory` and run with zero secrets/SDKs.
- `compileall` only compiles (never executes) the `app*.py` files, so missing env vars don't break CI.
- **Keep this boundary intact**: never let `core.py` / `persona.py` / `namespaces.py` import heavy SDKs or read env, and route new external calls through injected callables.

**Single-source modules prevent whole classes of bugs:**
- `slackbot/persona.py` — the gyaru tone lives here only. Both handlers import it (`BASE_SYSTEM_PROMPT` for DIY, `STRANDS_SYSTEM_PROMPT` for Strands). Change tone in one place. The persona prompt deliberately keeps a guardrail that technical content/code/commands stay accurate regardless of tone.
- `slackbot/namespaces.py` — AgentCore memory namespaces. Both the *registration* side (`scripts/create_memory.py`) and the *retrieval* side (`app_strands.py`) resolve from these constants, so they can't drift apart (a mismatch would silently make long-term memory never match). `tests/test_unit.py` asserts this equality.

**`core.build_conversation` enforces Bedrock Converse constraints.** It converts a Slack thread into Converse `messages` that must be: non-empty, start with `user`, and strictly alternate roles. It drops subtype/empty messages, merges consecutive same-role turns, strips a leading assistant message, caps to `MAX_HISTORY`, and falls back to a greeting if empty. Any change here must preserve those invariants (the unit + scenario tests check them).

**Memory ordering (DIY version).** A turn posts the reply *first*, then updates long-term memory — so memory extraction latency is never user-visible. `tests/test_scenario.py` asserts post-before-save. Short-term memory is the Slack thread itself (re-read each turn, never stored); long-term memory is per-user (DynamoDB / AgentCore), so it carries across threads but threads stay isolated.

## Conventions

- Code comments and docstrings are in Japanese; match that when editing existing files.
- When moving/renaming a module, update in lockstep: the Lambda handler path, README packaging/handler references, CI `compileall` paths, and test imports. `pyproject.toml` sets `pythonpath = ["."]` so `slackbot` and `scripts` resolve from repo root.
