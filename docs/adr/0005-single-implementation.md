# ADR-0005: DIY/Lambda 二重実装をやめ ECS 単一構成にする

- Status: Accepted
- Date: 2026-06-26

## Context

リポジトリは元々2実装を併存させていた:

- **DIY 版**(`app.py`): DynamoDB + 自前の抽出プロンプトで長期記憶。boto3/slack-bolt のみ(軽量)。
- **Strands 版**(`app_strands.py`): AgentCore Memory(自動抽出)。strands/bedrock-agentcore(重い)。

[ADR-0001](0001-ecs-fargate-socket-mode.md) で ECS Fargate + Socket Mode(`socket_app.py`)へ移行し、
CI/CD も ECS 専用に作り替えた結果、**両 Lambda ハンドラはどこからもデプロイされない死んだ経路**になった。
SAM/Lambda の成果物(`template.yaml` / `samconfig.toml` / `Dockerfile`)も同様。二重実装の維持は
学習コストとドリフト源にしかならなくなった。

## Decision

**ライブの ECS + Socket Mode + Strands 経路だけを残し、それ以外を削除する。**

- 削除: `slackbot/app.py`、`slackbot/app_strands.py`、`template.yaml`、`samconfig.toml`、
  `Dockerfile`(Lambda ベース)、`requirements.txt`(DIY 依存)、`tests/test_scenario.py`(DIY シナリオ)。
- `core.py` から Lambda 専用関数(`build_conversation` / `build_system_prompt` / `prepare_reply` /
  `update_long_term_memory` / `TurnResult` / `clean_mention` / `is_slack_retry`)を除去。
- `persona.py` の DIY 用 `BASE_SYSTEM_PROMPT` を削除し `STRANDS_SYSTEM_PROMPT` に一本化。
- README / CLAUDE.md を ECS + Socket Mode 単一構成として書き直し。

## Consequences

- 利点: リポジトリが「今動いている実態」と完全一致し、読み手の認知負荷が下がる。テストは27件に整理。
- トレードオフ: 「軽量 Lambda で安く始める」スケルトン的価値は失う。必要になれば git 履歴
  (commit `c072afd` 以前)から DIY 実装を復元できる。
- 影響: `slackbot/` は core/namespaces/persona(純粋)+ socket_app/strands_runtime/user_store(I/O)の
  6モジュールに収束。
