# Slack Bot on Lambda + Bedrock — 最小スケルトン

[![CI](https://github.com/ChiePro/senior-engineer-gyaru-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/ChiePro/senior-engineer-gyaru-bot/actions/workflows/ci.yml)


`@ボット名 質問` でメンションすると、Bedrock が生成した回答をスレッドに返す
社内向け Slack ボットの最小構成です。

## 構成は2系統

このリポジトリには2つの実装があります。用途で選んでください。

| | `app.py` (DIY 版) | `app_strands.py` (Strands 版) |
|---|---|---|
| 短期記憶 | スレッド履歴を手動取得 | session_id にスレッドを渡すだけ |
| 長期記憶 | DynamoDB + 自前の抽出プロンプト | actor_id にユーザーを渡すだけ (自動抽出) |
| 依存 | boto3, slack-bolt のみ (軽量) | strands-agents, bedrock-agentcore (重め) |
| 追加コスト | DynamoDB (ほぼ無料) | AgentCore Memory (従量課金の managed サービス) |
| 向き | とにかく安く最小で始めたい | 設計を綺麗に保ち拡張していきたい |

以下のセットアップ手順は主に DIY 版 (`app.py`) のものです。
Strands 版の追加手順は末尾「Strands 版セットアップ」を参照。

## アーキテクチャ

```
Slack ── Event ──> API Gateway ──> Lambda(app.handler)
                                      │ 1. ack() を即返す(3秒ルール対策)
                                      │ 2. 自分自身を非同期invoke (lazy listener)
                                      └─> 2回目のinvoke: Bedrock呼び出し → chat.postMessage
```

非同期の再invoke (lazy listener) を使うため、**Lambda は自分自身を invoke できる
IAM 権限が必要**です。これがないと回答が返りません。

## 1. Slack アプリの作成

1. https://api.slack.com/apps で「Create New App」(From scratch)
2. **OAuth & Permissions** > Bot Token Scopes に以下を追加
   - `app_mentions:read`
   - `chat:write`
   - `channels:history` (スレッド履歴の取得に必要 / マルチターン用)
   - `groups:history` (プライベートチャンネルでも使う場合)
3. ワークスペースにインストールして **Bot User OAuth Token** (`xoxb-...`) を取得
4. **Basic Information** > **Signing Secret** を控える
5. **Event Subscriptions** を ON にし、Subscribe to bot events に `app_mention` を追加
   - Request URL は後述のデプロイ後の API Gateway URL を設定
     (URL検証のため先にデプロイしておく)

## 2. パッケージング & デプロイ

依存込みで ZIP を作る例:

```bash
pip install -r requirements.txt -t ./package
cp app.py ./package/
cd package && zip -r ../function.zip . && cd ..
```

この `function.zip` を Lambda にアップロードし、ハンドラを `app.handler` に設定。
ランタイムは Python 3.12 以降を推奨。タイムアウトは 30 秒程度に。

> SAM / CDK / Serverless Framework を使う場合も、ハンドラ `app.handler` と
> 下記の環境変数・IAM 権限は共通です。

## 3. 環境変数

| キー | 例 | 説明 |
|------|-----|------|
| `SLACK_BOT_TOKEN` | `xoxb-...` | Bot User OAuth Token |
| `SLACK_SIGNING_SECRET` | `abc123...` | 署名検証用 |
| `BEDROCK_REGION` | `us-east-1` | 使いたいモデルがあるリージョン |
| `BEDROCK_MODEL_ID` | `us.amazon.nova-micro-v1:0` | 応答用モデル。Bedrock コンソールで要確認 |
| `MEMORY_MODEL_ID` | `us.amazon.nova-micro-v1:0` | (任意) 長期記憶の抽出用。未指定なら `BEDROCK_MODEL_ID`。安価モデル推奨 |
| `MEMORY_TABLE` | `slackbot-user-memory` | 長期記憶を保存する DynamoDB テーブル名 |

## 4. IAM 権限 (Lambda 実行ロール)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel"],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": ["lambda:InvokeFunction"],
      "Resource": "arn:aws:lambda:*:*:function:<この関数名>"
    },
    {
      "Effect": "Allow",
      "Action": ["dynamodb:GetItem", "dynamodb:PutItem"],
      "Resource": "arn:aws:dynamodb:*:*:table/<MEMORY_TABLE>"
    }
  ]
}
```

`lambda:InvokeFunction` は lazy listener が自分を再呼び出しするために必須。
`dynamodb:GetItem/PutItem` は長期記憶の読み書きに必要。
`Resource` は本番ではそれぞれの ARN に絞ってください。

## 5. DynamoDB テーブル (長期記憶)

オンデマンド課金で作成すると、低トラフィックではほぼ無料。

```bash
aws dynamodb create-table \
  --table-name slackbot-user-memory \
  --attribute-definitions AttributeName=user_id,AttributeType=S \
  --key-schema AttributeName=user_id,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST
```

- パーティションキー `user_id` (Slack のユーザーID) に対し、属性 `memory` (テキスト) を保存。
- 短期記憶はスレッド側に残るので DB には入れない。長期記憶だけここに置く。

## 6. モデルの有効化

Bedrock コンソール > **モデルアクセス** で使いたいモデルを有効化してから
`BEDROCK_MODEL_ID` を設定します。最新モデルは US リージョンが先行するため、
東京リージョンで未提供ならクロスリージョン推論プロファイル(`us.` 始まりのID)を利用。

## メモリ構造

- **短期記憶**: Slack スレッドの会話履歴。`conversations.replies` で都度読み直すだけで、
  DB に保存しない。スレッドが続く限り文脈が引き継がれる。
- **長期記憶**: ユーザーごとの趣味嗜好・性格などを DynamoDB に永続化。
  メンション時に system prompt へ注入し、返信後に今回の発言から抽出・統合して保存する。
  抽出は別の(安価な)モデル呼び出しで行われるため、1 ターンあたり Bedrock 呼び出しが
  2 回になる点に注意(コストを抑えたい場合は下記の「拡張の足がかり」参照)。

## 拡張の足がかり

- **記憶更新の頻度を下げる**: 今は毎ターン長期記憶を更新している。コスト削減のため、
  数ターンに 1 回だけ更新する / 一定文字数を超えた発言のみ対象にする等が有効。
- **記憶のTTL・上限管理**: `MAX_MEMORY_CHARS` で頭打ちにしているが、古い項目の整理や
  TTL 属性での自動失効を入れると肥大化を防げる。
- **記憶の確認・編集コマンド**: 「何を覚えてる?」で現在の長期記憶を表示、
  「忘れて」で削除、といったコマンドを足すと運用しやすい。
- **モデル切り替え**: Converse API なので `BEDROCK_MODEL_ID` を変えるだけ。
  応答は安価な Nova Micro / Claude Haiku で始め、必要なら Sonnet 系へ。
- **DM 対応**: `message.im` イベントを追加(`im:history`, `im:read` スコープも要追加)。

---

## Strands 版セットアップ (`app_strands.py`)

DIY 版の Slack アプリ作成・デプロイ・3秒ルール対策はそのまま流用できます。
差分は「DynamoDB の代わりに AgentCore Memory を使う」点です。

### 1. 依存

```bash
pip install -r requirements_strands.txt -t ./package
cp app_strands.py ./package/
```

Strands + AgentCore は依存が大きく Lambda の ZIP サイズ上限に当たりやすいので、
**Lambda レイヤー** または **コンテナイメージ** でのデプロイを検討してください。

### 2. AgentCore Memory リソースを1度だけ作成

付属の `create_memory.py` を実行します。嗜好・事実・要約の3戦略と、各 namespace、
短期記憶イベントの TTL を明示してあり、`app_strands.py` の `retrieval_config` と
namespace が一致するように作ってあります。

```bash
pip install bedrock-agentcore
BEDROCK_REGION=us-east-1 EVENT_EXPIRY_DAYS=30 python create_memory.py
```

実行すると `MEMORY_ID = ...` が出力されるので、その値を `app_strands.py` の
環境変数 `MEMORY_ID` に設定します。

namespace の対応(`create_memory.py` と `app_strands.py` で一致):

| 戦略 | namespace | app 側の用途 |
|------|-----------|------|
| userPreferenceMemoryStrategy | `/users/{actorId}/preferences/` | 趣味嗜好・性格 (retrieval 対象) |
| semanticMemoryStrategy | `/users/{actorId}/facts/` | 事実情報 (retrieval 対象) |
| summaryMemoryStrategy | `/users/{actorId}/summaries/{sessionId}/` | スレッド要約 (保存のみ) |

> namespace は末尾スラッシュまで含めて完全一致させること。`{actorId}` は実行時に
> Slack ユーザーIDへ置換される。ここが食い違うと長期記憶が検索でヒットしません。

`EVENT_EXPIRY_DAYS` が短期記憶イベントの保持日数(TTL, 7〜365日)です。
長期記憶レコードは抽出後も残るため、肥大化が気になる場合は定期的なレコード整理を別途検討してください。

### 3. 環境変数 (Strands 版)

| キー | 例 | 説明 |
|------|-----|------|
| `SLACK_BOT_TOKEN` | `xoxb-...` | DIY 版と同じ |
| `SLACK_SIGNING_SECRET` | `...` | DIY 版と同じ |
| `BEDROCK_REGION` | `us-east-1` | モデル/Memory のリージョン |
| `BEDROCK_MODEL_ID` | `us.amazon.nova-micro-v1:0` | 応答用モデル |
| `MEMORY_ID` | `SlackBotMemory-xxxxx` | 手順2で作成した Memory のID |

`MEMORY_TABLE` / `MEMORY_MODEL_ID` は Strands 版では不要です。

### 4. IAM 権限 (Strands 版で追加)

`dynamodb:*` の代わりに AgentCore Memory への権限が必要です。

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock-agentcore:CreateEvent",
    "bedrock-agentcore:ListEvents",
    "bedrock-agentcore:RetrieveMemoryRecords"
  ],
  "Resource": "arn:aws:bedrock-agentcore:*:*:memory/<MEMORY_ID>"
}
```

> アクション名・リソース形式は SDK / サービスのバージョンで変わりうるため、
> 最新のドキュメントで確認してください。

### 補足

- `actor_id` = Slack ユーザーID にすることで長期記憶が「人」に紐づき、
  別スレッド・別日でも本人の嗜好が引き継がれます。
- `session_id` = スレッド `thread_ts` にすることで短期記憶がスレッド単位になります。
- 長期記憶の抽出はバックグラウンド処理なので、直後の会話には即反映されないことがあります。
- Strands と AgentCore は更新が速いため、`AgentCoreMemoryConfig` / `RetrievalConfig` の
  パラメータ名はインストール版のドキュメントで確認してください。

---

## テスト

純粋ロジックは `bot_core.py` / `namespaces.py` に分離してあり、boto3・slack・strands を
インストールしなくても検証できます(これらに依存する import は遅延化/分離済み)。

```bash
pip install pytest
python -m pytest tests/ -v
```

- `tests/test_unit.py` — 単体テスト
  - メンション除去、Slack リトライ判定
  - `build_conversation`: role 交互化・連続結合・先頭 assistant 除去・subtype/空除外・履歴上限・空フォールバック
  - `build_system_prompt`: 長期記憶の有無による分岐
  - namespace: `resolve` の置換、末尾スラッシュ、**登録側(create_memory)と検索側(app)の一致**
- `tests/test_scenario.py` — シナリオテスト(マルチターン会話)
  - ターンをまたいだ長期記憶の蓄積と system prompt への注入
  - スレッド単位の短期記憶分離(別スレッドに履歴が混ざらない)
  - 「返信が先・長期記憶更新が後」の順序保証
  - 履歴上限で切り詰めても Converse 制約(user 始まり・role 交互)を維持

> 注: Strands 版 `app_strands.py` の応答生成自体は SDK と AgentCore Memory に委譲されるため、
> ここでは namespace 整合・メンション除去・リトライ判定など app 側ロジックを検証します。
> SDK 連携の動作確認は実環境(Memory リソース作成後)での結合テストで行ってください。
