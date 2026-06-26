---
name: agentcore-memory-ops
description: Operate the gyaru bot's memory — AgentCore Memory (short/long-term) and the DynamoDB user store (nicknames/traits/mood). Use when the user wants to create the memory resource, inspect or reset what the bot remembers about a person, debug "the bot keeps saying the same thing" or "it confused who someone is", or fix a junk/garbled stored row.
---

# メモリ運用(AgentCore Memory + DynamoDB ユーザーストア)

メモリは三層([docs/design/memory-model.md](../../../docs/design/memory-model.md))。`us-east-1`、`AWS_PROFILE=gyaru-admin`。

| 層 | 実体 | キー |
|---|---|---|
| 短期(スレッド) | AgentCore Memory events | `session_id = safe_id(thread_ts)` |
| 長期(人・横断) | AgentCore Memory records(preferences/facts) | `actor_id = safe_id(user_id)` |
| あだ名・特徴・機嫌 | DynamoDB ユーザーテーブル(`ecs.yaml` 作成) | `user_id`(対象者) |

## AgentCore Memory リソースの作成(初回1度)

```bash
pip install bedrock-agentcore
BEDROCK_REGION=us-east-1 EVENT_EXPIRY_DAYS=30 python -m scripts.create_memory
# 出力された MEMORY_ID を GitHub secret と ecs.yaml/ECS 環境変数に設定
```
現行 `MEMORY_ID = SlackBotMemory-uefTsX9os8`。namespace は `slackbot/namespaces.py` 単一ソース
(`/users/{actorId}/preferences/`・`/facts/`・`/summaries/{sessionId}/`)。登録側と検索側の一致は
`tests/test_unit.py` が保証。**末尾スラッシュまで完全一致**しないと長期記憶が永久にヒットしない。

## ある人の記憶を覗く / リセットする

```bash
# 長期記憶(events / records)を見る
MEM=SlackBotMemory-uefTsX9os8
aws bedrock-agentcore list-events --memory-id $MEM --actor-id <safe_idされたUserID> --region us-east-1
# リセットしたいときは該当 actor の events を削除(ループ・誤学習の解消)
```
※ `safe_id` 整形後の actor_id を使う(User ID にドット等が無ければそのまま)。

## DynamoDB ユーザーストア(あだ名・特徴・機嫌)

テーブル名は `ecs.yaml` の `USER_TABLE`(`aws ecs describe-task-definition` の env で確認)。
PK=`user_id`、属性 `nickname`(str)/ `cold`(bool)/ `notes`(list)。

```bash
TBL=<USER_TABLE>
aws dynamodb get-item --table-name $TBL --key '{"user_id":{"S":"U123..."}}' --region us-east-1
aws dynamodb delete-item --table-name $TBL --key '{"user_id":{"S":"U123..."}}' --region us-east-1  # その人の状態を消す
aws dynamodb update-item --table-name $TBL --key '{"user_id":{"S":"U123..."}}' \
  --update-expression "SET cold = :c" --expression-attribute-values '{":c":{"BOOL":false}}' --region us-east-1  # 塩対応解除
```

## よくある不具合と対処

- **「同じことしか言わない / 謝罪ループ」**: モデルがツール引数に壊れた値(`</user_id>`・`<@U..>`)を
  渡し、ゴミ行を作っているか、特定 actor の events が誤学習で固まっている。
  → `core.normalize_slack_id` がゴミを弾く(`test_unit` 参照)。既存のゴミ DynamoDB 行は delete-item で削除、
    暴走 actor は AgentCore events を整理。
- **「発言者と他人を取り違える(自分のあだ名を他人のだと覚える)」**: あだ名・特徴は **対象者 ID** キーで
  保存する設計。本文中の第三者 `<@Uxxx>` は `strip_bot_mention` で残し、`set_nickname(user_id=Uxxx,...)` を
  対象者 ID で呼ばせる。発言者本人の話なら発言者 ID にフォールバック。判断経緯は
  [docs/adr/0002-per-target-user-store.md](../../../docs/adr/0002-per-target-user-store.md)。
- **長期記憶が全くヒットしない**: namespace 不一致を疑う。登録(create_memory)と検索(retrieval_config)が
  `namespaces.py` から解決されているか、末尾スラッシュまで一致しているか。
