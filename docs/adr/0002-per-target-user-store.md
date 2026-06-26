# ADR-0002: あだ名・特徴・機嫌を「対象者ID」キーの DynamoDB に持つ

- Status: Accepted
- Date: 2026-06-26

## Context

「`@坂本` は坂もっちゃんっていうんだ」とボットに教えると、ボットが**発言者自身**のあだ名として
覚えてしまう取り違えが起きた。また、人物の特徴(役割・得意分野・個性)はワークスペース全員で
共有したい(誰が話しても「その人がどういう人か」は同じ)という要望があった。

AgentCore Memory は `actor_id`(=発言者)単位で長期記憶を持つ。これは「発言者本人の嗜好」には
合うが、「第三者の属性」や「全員共有のプロフィール」には構造的に合わない(発言者ごとに分かれてしまう)。

## Decision

**あだ名・特徴(notes)・機嫌(cold)は、AgentCore とは別の DynamoDB ユーザーストア
(`slackbot/user_store.py`)に、「対象ユーザーの Slack ID」をキーで持つ。**

- 本文中の第三者メンション `<@Uxxx>` は `core.strip_bot_mention` で残し(ボット自身のメンションだけ除去)、
  モデルに「誰のあだ名か」を ID で判別させる。
- モデルは function calling ツール `set_nickname` / `remember_about` / `set_mood` 経由で読み書きする。
  各ツールは `core.normalize_slack_id` で壊れた引数(`<@U..>`・`</user_id>` 等)から正規 ID を抽出し、
  取れなければ**発言者 ID にフォールバック**する。
- テーブルは PK=`user_id` の単純 KVS(`nickname` / `cold` / `notes[list]`)。`ecs.yaml` が作成。

## Consequences

- 利点: 発言者と第三者の取り違えが消え、人物プロフィールがワークスペース横断で共有される。
- 利点: 機嫌(塩対応)を相手単位で持てる(失礼を言った人だけ冷たく、謝れば解除)。
- トレードオフ: 記憶が AgentCore(嗜好・事実)と DynamoDB(あだ名・特徴・機嫌)の2系統に分かれる。
  → どちらに何を置くかは [docs/design/memory-model.md](../design/memory-model.md) に明文化。
- 注意: ツールの docstring と `persona.BEHAVIOR_GUIDE` は揃えて保つ(モデルは両方読む)。
- 関連運用: `agentcore-memory-ops` スキル(覗き方・リセット・ゴミ行掃除)。
