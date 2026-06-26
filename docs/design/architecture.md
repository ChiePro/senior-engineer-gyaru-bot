# システム設計: senior-engineer-gyaru-bot

社内 Slack ボット。`@bot 質問` で Bedrock 生成の回答をスレッドに返す。中身はシニアエンジニア、
口調は今どきギャル。ECS Fargate 常駐 + Slack Socket Mode。決定の経緯は [../adr/](../adr/README.md)。

## 全体像

```
Slack ──(outbound WebSocket / Socket Mode)── ECS Fargate Service (desiredCount=1)
                                                python -m slackbot.socket_app
                                                  ├─ Bedrock (応答生成: gpt-oss-120b)
                                                  ├─ AgentCore Memory (短期=スレッド / 長期=人)
                                                  └─ DynamoDB (あだ名・特徴・機嫌 / 対象者ID)
SSM Parameter Store ──(起動時に実行ロールが解決)── SLACK_BOT_TOKEN / SLACK_APP_TOKEN
```

- 受信用の公開エンドポイントが無い(Socket Mode)。API Gateway / URL 検証 / 署名検証 / 自己 invoke は不要。
- 常駐なのでコールドスタートが無く、Slack の 3 秒ルールに常に間に合う([ADR-0001](../adr/0001-ecs-fargate-socket-mode.md))。
- 1 接続 = 1 タスク。ローリングは旧タスク停止 → 新タスク起動(二重接続=二重応答の防止)。

## モジュール構成と責務

| ファイル | レイヤー | 依存 | 責務 |
|---|---|---|---|
| `slackbot/core.py` | 純粋 | stdlib | メンション整形 / 人物注記 / 内部タグ除去 / Slack ID 整形(`safe_id`/`normalize_slack_id`) |
| `slackbot/namespaces.py` | 純粋 | stdlib | AgentCore namespace の単一ソース + `resolve()` |
| `slackbot/persona.py` | 純粋 | stdlib | 人格(口調・ふるまい・塩対応・フォールバック)の単一ソース |
| `slackbot/strands_runtime.py` | I/O | strands, bedrock-agentcore | `respond()`: Agent 構築・ツール定義・記憶配線・応答生成 |
| `slackbot/socket_app.py` | I/O | slack-bolt | Socket Mode 常駐エントリ。`app_mention` を受けて `respond()` を呼ぶ |
| `slackbot/user_store.py` | I/O | boto3 | DynamoDB のあだ名・特徴・機嫌ストア |
| `scripts/create_memory.py` | 運用 | bedrock-agentcore | AgentCore Memory を1度だけ作る(デプロイ対象外) |

純粋/I/O の分離理由は [ADR-0004](../adr/0004-pure-logic-io-separation.md)。これによりテストは
secret も重い SDK も不要で回る。

## 1 メンションの処理フロー

1. `socket_app.handle_mention` が `app_mention` を受信。
2. `core.strip_bot_mention` でボット自身のメンションだけ除去(第三者 `<@Uxxx>` は残す)。
   `core.mentioned_user_ids` で本文中の対象者 ID を抽出。
3. `user_store.profiles_for([発言者]+対象者)` であだ名・特徴を、`get(発言者)["cold"]` で機嫌を引く。
4. `strands_runtime.respond()`:
   - `actor_id = safe_id(user_id)` / `session_id = safe_id(thread_ts)` で AgentCore に紐付け。
   - system prompt = `STRANDS_SYSTEM_PROMPT` + 発言者 ID + `BEHAVIOR_GUIDE` + 人物注記
     (+ 塩対応なら `COLD_MODE_NOTE`)。
   - Strands `Agent`(`BedrockModel` + `AgentCoreMemorySessionManager` + ツール3種)を構築、
     `callback_handler=None`。
   - 返信は `strip_internal_tags(str(agent(text)))`。
5. `say(text=返信, thread_ts=...)` でスレッドに投稿。

## 失敗時の挙動

- 応答生成が例外 → `persona.FALLBACK_MESSAGE`(キャラを保った謝罪)を返す。
- ストア読み取りが例外 → プロフィール空・塩対応 false にフォールバックして続行。
- `auth_test` 失敗 → `BOT_USER_ID=None`(メンション除去がやや劣化するが落ちない)。

## デプロイ / CI/CD

- インフラ: `ecs.yaml`(CloudFormation, スタック `gyaru-bot-ecs`)。LogGroup / DynamoDB テーブル /
  Cluster / 実行・タスクロール / egress-only SG / TaskDefinition / Service。
- パイプライン: push→CI(compileall+ruff+pytest)→ 成功で deploy.yml(OIDC → ECR build/push[SHA]→
  cloudformation deploy → services-stable)。`main` は保護([ADR-0006](../adr/0006-cicd-and-branch-protection.md))。

## メモリ

三層構造の詳細は [memory-model.md](memory-model.md)。
