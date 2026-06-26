# Senior Engineer Gyaru Bot — ECS Fargate + Bedrock

[![CI](https://github.com/ChiePro/senior-engineer-gyaru-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/ChiePro/senior-engineer-gyaru-bot/actions/workflows/ci.yml)

`@ボット名 質問` でメンションすると、Amazon Bedrock が生成した回答をスレッドに返す
社内向け Slack ボットの最小構成です。

中身はシニアエンジニア、でも口調はギャル — というキャラ付け(ほどよくギャル)を
してあります。技術的な内容・コード・コマンドは正確に保ちつつ、地の口調だけ
フランクにします。口調を変えたい/真面目なアシスタントに戻したいときは
`slackbot/persona.py` を編集するだけです。

**ECS Fargate 上に常駐し、Slack Socket Mode で動きます。** 公開エンドポイント・
API Gateway・URL 検証・Lambda のコールドスタートはいずれも不要です。

## ディレクトリ構成

```
slackbot/              # ECS コンテナに載せる本体パッケージ
  core.py              # I/O を持たない純粋ロジック(メンション整形・人物注記・タグ除去・ID整形)
  namespaces.py        # AgentCore namespace の単一ソース(登録側と検索側の食い違い防止)
  persona.py           # 口調(ギャル人格)の単一ソース ← キャラ変更はここだけ
  user_store.py        # あだ名・特徴・機嫌を対象ユーザーID単位で持つ DynamoDB ストア
  strands_runtime.py   # 応答生成(Strands Agent + AgentCore Memory + function calling ツール)
  socket_app.py        # Socket Mode 常駐エントリ → python -m slackbot.socket_app
scripts/
  create_memory.py     # AgentCore Memory を1度だけ作る運用スクリプト(デプロイ対象外)
ecs.yaml               # ECS Fargate スタック(CloudFormation)
Dockerfile.fargate     # 常駐コンテナのイメージ
infra/
  github-oidc-bootstrap.yaml  # GitHub OIDC + デプロイ用 IAM ロール(初回1度だけ)
.github/workflows/     # ci.yml(lint+test)/ deploy.yml(build→push→deploy)
tests/                 # 重い依存なしで動く単体・ペルソナテスト
```

## アーキテクチャ

```
Slack ──(outbound WebSocket / Socket Mode)── ECS Fargate Service(常時1タスク)
                                                  python -m slackbot.socket_app
                                                  └─> Bedrock(応答生成)
                                                  └─> AgentCore Memory(長期記憶)
                                                  └─> DynamoDB(あだ名・特徴・機嫌)
```

- **Socket Mode** は Slack へ outbound の WebSocket を張る方式。受信用の公開 URL が要らないので、
  API Gateway・URL 検証・署名検証(signing secret)・Lambda の自己 invoke がすべて不要。
- **ECS Fargate** で常駐するためコールドスタートが無く、Slack の 3 秒ルールに常に間に合う。
- Socket Mode は 1 接続 = 1 タスク前提なので、サービスは **desiredCount=1**、デプロイは
  「旧タスクを止めてから新タスクを起動」(二重接続を避ける)。
- default VPC の public subnet + `assignPublicIp` で outbound するだけなので、ALB / NAT は不要。

## メモリ構造

- **短期記憶(スレッド単位)**: AgentCore Memory の `session_id` = スレッド `thread_ts`。
  同じスレッドの文脈が引き継がれ、別スレッドとは混ざらない。
- **長期記憶(人単位・横断)**: AgentCore Memory の `actor_id` = Slack ユーザーID。
  趣味嗜好・事実が自動抽出され、別スレッド・別日でも本人に紐づいて引き継がれる。
- **あだ名・特徴・機嫌(横断・ワークスペース共有)**: DynamoDB に **対象ユーザーID** をキーで保存。
  「その人がどういう人か」はワークスペース全員で共有される情報なので、発言者単位の AgentCore
  メモリではなくここに置く。モデルは function calling ツール(`set_nickname` / `remember_about` /
  `set_mood`)経由で読み書きする。

## 1. Slack アプリの作成(Socket Mode)

1. https://api.slack.com/apps で「Create New App」(From scratch)。App 名が Slack 上の表示名になる。
2. **Socket Mode** を ON にする。
3. **OAuth & Permissions** > Bot Token Scopes に追加:
   - `app_mentions:read`
   - `chat:write`
4. **Event Subscriptions** を ON にし、Subscribe to bot events に `app_mention` を追加
   (Socket Mode なので Request URL の設定は不要)。
5. ワークスペースにインストールして **Bot User OAuth Token**(`xoxb-...`)を取得。
6. **Basic Information** > App-Level Tokens で、スコープ `connections:write` の
   **App-Level Token**(`xapp-...`)を発行する(Socket Mode の接続に必須)。

## 2. AgentCore Memory を1度だけ作成

```bash
pip install bedrock-agentcore
# リポジトリルートからモジュールとして実行(slackbot.namespaces を解決するため)
BEDROCK_REGION=us-east-1 EVENT_EXPIRY_DAYS=30 python -m scripts.create_memory
```

実行すると `MEMORY_ID = ...` が出力されるので、その値を後述の `MEMORY_ID` に設定します。
3 戦略(嗜好・事実・要約)と各 namespace、短期記憶イベントの TTL(`EVENT_EXPIRY_DAYS`, 7〜365日)を
明示してあり、`strands_runtime.py` の `retrieval_config` と namespace が一致するように作られています。

| 戦略 | namespace | 用途 |
|------|-----------|------|
| userPreferenceMemoryStrategy | `/users/{actorId}/preferences/` | 趣味嗜好・性格(retrieval 対象) |
| semanticMemoryStrategy | `/users/{actorId}/facts/` | 事実情報(retrieval 対象) |
| summaryMemoryStrategy | `/users/{actorId}/summaries/{sessionId}/` | スレッド要約(保存のみ) |

> namespace は末尾スラッシュまで含めて完全一致させること。食い違うと長期記憶が検索でヒットしません。
> `tests/test_unit.py` が登録側(create_memory)と検索側(namespaces)の一致を検査しています。

## 3. シークレットを SSM Parameter Store に格納

Slack のトークンは SSM の SecureString に置き、ECS タスクが起動時に解決します(CI には渡しません)。

```bash
aws ssm put-parameter --name /gyaru-bot/SLACK_BOT_TOKEN --type SecureString --value "xoxb-..."
aws ssm put-parameter --name /gyaru-bot/SLACK_APP_TOKEN --type SecureString --value "xapp-..."
# 値を更新するときは --overwrite を付ける
```

> スケルトン簡略化のため SSM 直参照にしています。本番では Secrets Manager + ローテーションを検討してください。

## 4. 環境変数(ECS タスク)

`ecs.yaml` がタスク定義に流し込みます(トークンは SSM 由来の Secrets、他は通常の環境変数)。

| キー | 例 | 説明 |
|------|-----|------|
| `SLACK_BOT_TOKEN` | `xoxb-...` | Bot User OAuth Token(SSM 由来) |
| `SLACK_APP_TOKEN` | `xapp-...` | App-Level Token(Socket Mode 接続用 / SSM 由来) |
| `BEDROCK_REGION` | `us-east-1` | モデル / Memory のリージョン |
| `BEDROCK_MODEL_ID` | `openai.gpt-oss-120b-1:0` | 応答用モデル |
| `MEMORY_ID` | `SlackBotMemory-xxxxx` | 手順2で作成した Memory のID |
| `USER_TABLE` | (自動) | あだ名・特徴・機嫌を保存する DynamoDB テーブル名(`ecs.yaml` が作成) |

## 5. デプロイ(CloudFormation)

イメージを ECR に push し、`ecs.yaml` をデプロイします。`ImageUri` は commit SHA タグ推奨
(TaskDefinition が変わり ECS が自動ローリングする)。

```bash
REPO=<account>.dkr.ecr.us-east-1.amazonaws.com/gyaru-bot
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin "${REPO%/*}"
docker build --platform linux/amd64 -f Dockerfile.fargate -t "${REPO}:$(git rev-parse HEAD)" .
docker push "${REPO}:$(git rev-parse HEAD)"

aws cloudformation deploy \
  --template-file ecs.yaml --stack-name gyaru-bot-ecs --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    "ImageUri=${REPO}:$(git rev-parse HEAD)" \
    "MemoryId=SlackBotMemory-xxxxx" \
    "BedrockRegion=us-east-1" "BedrockModelId=openai.gpt-oss-120b-1:0" \
    "VpcId=vpc-xxxx" "SubnetIds=subnet-a,subnet-b"
```

`ecs.yaml` は LogGroup / DynamoDB ユーザーテーブル / Cluster / 実行・タスクロール /
egress-only セキュリティグループ / TaskDefinition / Service(desiredCount=1, assignPublicIp)を作ります。

## 6. モデルの有効化

Bedrock のモデルは初回 InvokeModel 時に自動で有効化されます(モデルアクセスページは廃止)。
最新モデルは US リージョンが先行するため、`BEDROCK_REGION=us-east-1` 推奨。クロスリージョン推論
プロファイル(`us.` 始まりのID)も利用できます。Anthropic のモデルはユースケース申請フォームの
提出が必要な場合があります。

## CI/CD(自動デプロイ)

`main` への push で **CI(lint + test)→ 成功したら自動で本番デプロイ** が走ります。

| ファイル | 役割 |
|---|---|
| `.github/workflows/ci.yml` | `compileall` + `ruff` + `pytest`(秘密情報不要) |
| `.github/workflows/deploy.yml` | detect(コスト変更判定)→ approval(コスト変更時のみ所有者承認)→ deploy(OIDC → ECR build/push → `cloudformation deploy ecs.yaml` → `ecs wait services-stable`) |
| `infra/github-oidc-bootstrap.yaml` | GitHub OIDC プロバイダ + デプロイ用 IAM ロール(初回1度だけ) |
| `Dockerfile.fargate` | 常駐コンテナのイメージ(`requirements_strands.txt` + `slackbot/`) |
| `ecs.yaml` | ECS Fargate スタック本体 |

```
push (main) ──> CI (compileall + ruff + pytest)
                   │ success
                   └─> Deploy (workflow_run)
                         detect: 差分に ecs.yaml が含まれるか(=お金に関わる変更か)
                           │
                           ├─ cost変更あり ─> approval: environment "production" で所有者の Approve を待つ
                           │
                           └─> deploy: OIDC assume → docker build --platform linux/amd64(SHA)→ ECR push
                                       → cloudformation deploy ecs.yaml → ecs wait services-stable
```

**お金に関わる変更の承認ゲート**: Fargate の CPU/メモリ/タスク数・DynamoDB 課金モード・Bedrock モデルは
すべて `ecs.yaml` で管理する。これらを変更すると `ecs.yaml` が差分に出て、デプロイ前に
リポジトリ所有者(`production` environment の required reviewer)の承認が要る。docs やアプリコードだけの
変更は従来どおり自動デプロイされる。承認ゲートを使うには **GitHub の `production` environment に
required reviewers を設定**しておくこと。

### 初回セットアップ

1. **OIDC ロールを作成**(1度だけ。ローカルから AWS 管理者権限で実行):

   ```bash
   aws cloudformation deploy \
     --template-file infra/github-oidc-bootstrap.yaml \
     --stack-name gyaru-bot-oidc \
     --capabilities CAPABILITY_NAMED_IAM \
     --parameter-overrides GitHubOrg=ChiePro GitHubRepo=senior-engineer-gyaru-bot DeployStackName=gyaru-bot-ecs
   # 出力 RoleArn を控える。既に OIDC プロバイダがある場合は CreateOIDCProvider=false を足す。
   ```

2. **AgentCore Memory を作成**して `MEMORY_ID` を取得(手順2)、**SSM にトークンを格納**(手順3)。

3. **GitHub に変数とシークレットを登録**(Settings > Secrets and variables > Actions):

   | 種別 | キー | 例 / 説明 |
   |------|------|-----------|
   | Variables | `AWS_DEPLOY_ROLE_ARN` | 手順1の出力 RoleArn |
   | Variables | `AWS_REGION` | デプロイ先リージョン(例 `us-east-1`) |
   | Variables | `BEDROCK_REGION` | Bedrock / Memory のリージョン |
   | Variables | `ECR_REPOSITORY` | ECR リポジトリ名(例 `gyaru-bot`) |
   | Variables | `VPC_ID` | タスクを置く VPC(default VPC でよい) |
   | Variables | `SUBNET_IDS` | public subnet をカンマ区切りで2つ以上 |
   | Secrets | `MEMORY_ID` | 手順2で作成した Memory のID |

   > Bedrock モデル ID は GitHub 変数ではなく `ecs.yaml` の `BedrockModelId` Default が単一ソース
   > (承認ゲートに乗せるため)。

4. **`production` environment に required reviewers を設定**(Settings > Environments)。
   お金に関わる変更(`ecs.yaml`)のデプロイ前承認に使う。
5. `main` に push すると CI → Deploy が走り、ECS サービスがローリング更新されます。

## ペルソナのカスタマイズ

口調・性格・ふるまいは `slackbot/persona.py` に集約しています。

- `PERSONA` — キャラの核。基本レジスターは常にタメ口だが、コード・コマンド・値などの
  「中身」は崩さず正確に保つ、というガードレール付き。
- `BEHAVIOR_GUIDE` — あだ名 / 特徴 / 機嫌(塩対応)ツールの使い方。保存は宣言せず裏方で行う方針。
- `COLD_MODE_NOTE` — 失礼を言われた相手にだけ塩対応し、誠実な謝罪で解除する注記。
- `FALLBACK_MESSAGE` — 応答生成に失敗したときの、キャラを保った返信。

## テスト

純粋ロジックは `slackbot/core.py` / `slackbot/namespaces.py`、口調は `slackbot/persona.py` に
分離してあり、boto3・slack・strands をインストールしなくても検証できます(重い依存は
`strands_runtime` / `socket_app` / `user_store` 側に隔離済み)。

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

- `tests/test_unit.py` — メンション整形(`strip_bot_mention` / `mentioned_user_ids`)、
  壊れたツール引数の救済(`normalize_slack_id`)、人物注記(`build_people_note`)、
  内部タグ除去(`strip_internal_tags`)、AgentCore ID 整形(`safe_id`)、
  namespace 整合(登録側と検索側の一致)。
- `tests/test_persona.py` — ギャル口調マーカーと技術的正確さのガードレールの両立、
  Strands 用プロンプトが核 + 長期記憶活用の一文になっていること、ツール名がガイドに揃っていること。

> 応答生成自体は Strands SDK と AgentCore Memory に委譲されるため、SDK 連携の動作確認は
> 実環境(Memory リソース作成後)での結合テストで行ってください。
