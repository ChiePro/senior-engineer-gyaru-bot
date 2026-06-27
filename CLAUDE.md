# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Slack bot that runs on **AWS ECS Fargate** (always-on container) and talks to **Amazon Bedrock**. Mention `@bot question` and it replies in the thread. It connects to Slack via **Socket Mode** (an outbound WebSocket), so there is no public endpoint, API Gateway, URL verification, or Lambda cold start. The bot's persona is a senior engineer who speaks in casual *gyaru* style — friendly tone, but technically accurate (code/commands are never garbled). It is intentionally a **minimal skeleton** meant to be forked and extended.

The entrypoint is `python -m slackbot.socket_app`, running as a single Fargate task.

## Commands

```bash
# Dev deps (pytest + ruff only — no boto3/slack/strands needed for tests)
pip install -r requirements-dev.txt

python -m pytest tests/                       # all tests
python -m pytest tests/test_persona.py        # one file
python -m pytest tests/test_unit.py::test_safe_id_replaces_dot_in_thread_ts  # one test
ruff check .                                  # lint (line-length 100, py312)
python -m compileall -q slackbot scripts      # syntax-check deployables (what CI runs)
```

CI (`.github/workflows/ci.yml`) runs exactly the last three steps. It needs **no secrets** — see "Pure logic is isolated from I/O" below.

```bash
# Validate the CloudFormation templates locally (cfn-lint via pip)
cfn-lint ecs.yaml infra/github-oidc-bootstrap.yaml

# Build/push image + deploy the ECS stack (see README for full parameter list)
docker build --platform linux/amd64 -f Dockerfile.fargate -t "$REPO:$(git rev-parse HEAD)" .
docker push "$REPO:$(git rev-parse HEAD)"
aws cloudformation deploy --template-file ecs.yaml --stack-name gyaru-bot-ecs --capabilities CAPABILITY_IAM \
  --parameter-overrides "ImageUri=$REPO:$(git rev-parse HEAD)" MemoryId=... BedrockRegion=... BedrockModelId=... VpcId=... SubnetIds=...
```

Build for `linux/amd64` (Fargate is x86; an arm64 Mac will otherwise produce an unrunnable image).

## Architecture that spans files

**Socket Mode, single always-on task.** `slackbot/socket_app.py` opens an outbound WebSocket to Slack and stays connected. There is no inbound HTTP, so no signing-secret verification and no self-invoke. Because Socket Mode is 1-connection-per-task, the ECS service runs **desiredCount=1** and deploys stop the old task before starting the new one (`MinimumHealthyPercent: 0`) to avoid double connections / duplicate replies.

**Pure logic is isolated from I/O so tests need no heavy deps.** `slackbot/core.py`, `namespaces.py`, and `persona.py` import only stdlib. All external I/O (Slack/Bedrock/AgentCore/DynamoDB) lives in `socket_app.py`, `strands_runtime.py`, and `user_store.py` — the only files that import boto3/slack/strands and read `os.environ`. Therefore:
- Tests import only `slackbot.core` / `namespaces` / `persona` / `scripts.create_memory` and run with zero secrets/SDKs.
- `compileall` only compiles (never executes) the I/O files, so missing env vars don't break CI.
- **Keep this boundary intact**: never let `core.py` / `persona.py` / `namespaces.py` import heavy SDKs or read env.

**Mention-less autoreply in engaged threads (ADR-0008).** Besides `app_mention`, `socket_app.handle_message` subscribes to `message` events so the bot can reply without a mention — but only in threads where it was mentioned at least once. The pure decision logic lives in `core.py` (`is_autoreply_candidate` cheap pre-filter → one `conversations.replies` call → `summarize_thread` → `classify_thread`): a thread with no prior bot mention is ignored; a thread with only the bot + the speaker replies every time; a thread with other humans is treated as a **group**, where the model participates actively but may emit `core.SKIP_TOKEN` (`<skip/>`) to stay silent (`is_silent_reply`/`strip_skip_token` gate the post). Messages that mention the bot are skipped here and handled by `app_mention` to avoid double replies. The group-participation policy (active + when to `<skip/>`) is single-sourced in `persona.GROUP_REPLY_GUIDE`; `respond(may_stay_silent=…, thread_context=…)` injects it plus the recent transcript (other people's messages aren't in AgentCore short-term memory). Requires Slack `message.channels` events + `channels:history` scope.

**Response generation is centralized in `strands_runtime.respond()`.** It builds a Strands `Agent` with a `BedrockModel`, an `AgentCoreMemorySessionManager`, and the function-calling tools. `callback_handler=None` is set deliberately: the default printing handler streams the model's reasoning to stdout (noisy in CloudWatch); the Slack reply is `str(agent(text))`, which returns only the text content block. `socket_app.handle_mention` is the thin Slack wiring around it.

**Single-source modules prevent whole classes of bugs:**
- `slackbot/persona.py` — the gyaru tone lives here only (`PERSONA`, `STRANDS_SYSTEM_PROMPT`, `BEHAVIOR_GUIDE`, `COLD_MODE_NOTE`, `FALLBACK_MESSAGE`). Change tone in one place. The persona keeps a guardrail that the base register is always casual *タメ口* while code/commands/values stay verbatim and accurate.
- `slackbot/namespaces.py` — AgentCore memory namespaces. Both the *registration* side (`scripts/create_memory.py`) and the *retrieval* side (`strands_runtime.py` `retrieval_config`) resolve from these constants, so they can't drift apart (a mismatch would silently make long-term memory never match). `tests/test_unit.py` asserts this equality.

**Three layers of memory, by design:**
- **Short-term (per thread)** — AgentCore `session_id = safe_id(thread_ts)`. Threads stay isolated.
- **Long-term (per person, cross-thread)** — AgentCore `actor_id = safe_id(user_id)`; preferences/facts auto-extracted and retrieved.
- **Nicknames / traits / mood (cross-cutting, workspace-shared)** — `user_store.py` (DynamoDB), keyed by the **target user's** Slack ID, not the speaker's. The model reads/writes it through the `set_nickname` / `remember_about` / `set_mood` tools built in `strands_runtime._build_tools`. `core.strip_bot_mention` + `mentioned_user_ids` keep third-party `<@Uxxx>` mentions intact so the model can tell *whose* nickname is being set; `core.normalize_slack_id` rescues garbled tool arguments and falls back to the speaker's ID.

**AgentCore ID constraints.** `actorId`/`sessionId` must match `[a-zA-Z0-9][a-zA-Z0-9-_]*`, but Slack `thread_ts` contains dots — `core.safe_id` sanitizes deterministically so the same thread always maps to the same session. `core.strip_internal_tags` is a last-resort scrub of `<thinking>`-style tags before posting to Slack (gpt-oss separates reasoning into `reasoningContent`, but tagged leakage from other models is still stripped).

## CI/CD

On push to `main`, `ci.yml` runs `compileall` + `ruff` + `pytest`. On its success, `deploy.yml` (triggered via `workflow_run`) authenticates to AWS via **GitHub OIDC**, builds the `linux/amd64` image tagged with the commit SHA, pushes to ECR, runs `cloudformation deploy ecs.yaml`, and waits with `ecs wait services-stable`. The SHA tag makes `ImageUri` change every deploy, so the TaskDefinition updates and ECS rolls automatically. Slack tokens are **not** passed through CI — they live in SSM Parameter Store and the ECS execution role resolves them at task start. The OIDC deploy role is provisioned once by `infra/github-oidc-bootstrap.yaml` (policy `ecs-container-deploy`: cloudformation/ecr/ecs/ec2-SG/dynamodb/logs, IAM role management scoped to `gyaru-bot-ecs-*`, PassRole to `ecs-tasks.amazonaws.com`).

GitHub repo config the pipeline reads: vars `AWS_DEPLOY_ROLE_ARN`, `AWS_REGION`, `BEDROCK_REGION`, `BEDROCK_MODEL_ID`, `ECR_REPOSITORY`, `VPC_ID`, `SUBNET_IDS`; secret `MEMORY_ID`.

## Conventions

- Code comments and docstrings are in Japanese; match that when editing existing files.
- When moving/renaming a module, update in lockstep: the entrypoint (`python -m slackbot.socket_app`), `Dockerfile.fargate`, CI `compileall` paths, README references, and test imports. `pyproject.toml` sets `pythonpath = ["."]` so `slackbot` and `scripts` resolve from repo root.
- `scripts/create_memory.py` is a one-time setup script (run as `python -m scripts.create_memory` from repo root); it is **not** deployed.
