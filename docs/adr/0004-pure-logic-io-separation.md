# ADR-0004: 純粋ロジックと I/O を分離しテストを secret 不要にする

- Status: Accepted
- Date: 2026-06-26

## Context

ボットは boto3 / slack-bolt / strands-agents / bedrock-agentcore という重い SDK に依存し、
実行には Slack/AWS の secret も要る。これらをテストやり CI で必要にすると、CI が遅く・壊れやすく・
secret 管理が必要になる。

## Decision

**stdlib だけに依存する純粋ロジック層と、SDK・env に触れる I/O 層を物理的に分ける。**

- 純粋ロジック: `slackbot/core.py`(文字列整形・人物注記・タグ除去・ID 整形)、
  `slackbot/namespaces.py`(namespace 定義)、`slackbot/persona.py`(人格)。stdlib のみ。
- I/O・配線: `slackbot/socket_app.py` / `strands_runtime.py` / `user_store.py`。
  ここだけが boto3/slack/strands を import し `os.environ` を読む。
- テストは純粋ロジック層 + `scripts.create_memory` だけを import する。
- CI は `compileall slackbot scripts`(コンパイルのみ・実行しない)+ `ruff` + `pytest` の3つ。

## Consequences

- 利点: **CI は secret も重い SDK も不要**で完結し、速くて壊れにくい。ロジックを純粋関数として
  単体テストできる。`compileall` が I/O ファイルを実行しないので env 未設定でも通る。
- 制約(守るべき不変条件): 純粋ロジック層に SDK import / env 読みを**絶対に持ち込まない**。
  新しい外部 I/O は I/O 層へ。SDK 連携の実挙動はユニットテストの対象外とし、実環境の結合確認で担保する。
- 関連: [.claude/rules/architecture.md](../../.claude/rules/architecture.md) / [.claude/rules/testing.md](../../.claude/rules/testing.md)。
