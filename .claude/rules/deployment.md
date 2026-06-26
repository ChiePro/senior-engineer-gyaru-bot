# デプロイ・運用規約

本番は **ECS Fargate(常駐1タスク)+ Slack Socket Mode**。リージョンは `us-east-1`。
スタック名 `gyaru-bot-ecs`(CloudFormation)。詳細手順は `deploy-and-rollback` スキル参照。

## 前提・不変条件

- **Socket Mode は 1 接続 = 1 タスク。** サービスは `desiredCount=1`、デプロイは
  `MinimumHealthyPercent: 0`(旧タスクを止めてから新タスク起動)。二重接続=二重応答を防ぐため。
- **イメージは必ず `--platform linux/amd64` でビルド。** Fargate は x86。arm64 Mac で素ビルドすると起動しない。
- **イメージタグは commit SHA。** `:latest` 運用だと CloudFormation が ImageUri 変化を検知せずローリングしない。
  SHA タグなら TaskDefinition が変わり自動ローリングする。
- **Slack トークンは SSM Parameter Store(SecureString)。** CI からは渡さない。
  `/gyaru-bot/SLACK_BOT_TOKEN` / `/gyaru-bot/SLACK_APP_TOKEN`。タスク実行ロールが起動時に解決する。
- **`main` への push は本番デプロイを意味する。** ブランチ保護で直 push は全員禁止、PR+承認+CI 必須。

## CI/CD

- `ci.yml`: push/PR で `compileall` + `ruff` + `pytest`(secret 不要)。
- `deploy.yml`: `workflow_run` で CI 成功時のみ起動。3ジョブ構成 = **detect**(差分に `ecs.yaml` が
  含まれるか=お金に関わる変更かを判定)→ **approval**(コスト変更時のみ、required reviewers 付き
  environment `production` を通って所有者の Approve を待つ)→ **deploy**(OIDC assume → ECR build/push
  [SHA] → `cloudformation deploy ecs.yaml` → `ecs wait services-stable`)。
- **お金に関わる変更は承認必須**: Fargate の CPU/メモリ/タスク数・DynamoDB 課金モード・Bedrock モデルは
  すべて `ecs.yaml` で管理する。これらを変えると `ecs.yaml` が差分に出て、デプロイ前に所有者承認が要る。
  詳細は [docs/adr/0007-cost-change-approval-gate.md](../../docs/adr/0007-cost-change-approval-gate.md)。
- OIDC ロールは `infra/github-oidc-bootstrap.yaml`(`ecs-container-deploy` ポリシー、
  PassRole は `ecs-tasks.amazonaws.com`、IAM ロール管理は `gyaru-bot-ecs-*` に限定)。

### GitHub に必要な設定

| 種別 | キー |
|---|---|
| Variables | `AWS_DEPLOY_ROLE_ARN` / `AWS_REGION` / `BEDROCK_REGION` / `ECR_REPOSITORY` / `VPC_ID` / `SUBNET_IDS` |
| Secrets | `MEMORY_ID` |

> Bedrock モデル ID は GitHub 変数ではなく **`ecs.yaml` の `BedrockModelId` Default** が単一ソース
> (モデル単価=お金に関わるため、ファイル差分として承認ゲートに乗せる)。`deploy.yml` は
> `BedrockModelId` を override しない。

## ローカルからの操作

- ローカル CLI は `AWS_PROFILE=gyaru-admin` を使う(`AmazonPollyCLI` 等の別ユーザーだと権限不足)。
- 手動デプロイ/ロールバック/モデル切替は各スキルの手順に従う。zsh では `${REPO}:latest` のように
  **波括弧で囲む**(`$REPO:latest` は `:l` 修飾子でタグが化ける)。

## セキュリティ運用

- secret はコードに書かない。トークンが露出したら必ずローテーション
  (Slack 再発行 → SSM `--overwrite` → `aws ecs update-service --force-new-deployment`)。
- 本番は production 扱い。push/デプロイ/外部反映は明示の承認があるときだけ実行する。
- **CI/ゲート自体の書き換え防止**: `.github/CODEOWNERS` で `.github/`・`ecs.yaml`・`infra/` を
  所有者の所有にし、ruleset の "require code owner review" を有効化している。承認ゲートや OIDC ロールを
  書き換える PR は所有者承認なしにはマージできない(コスト承認ゲートは `ecs.yaml` しか見ないので、
  ゲート定義そのもの=`deploy.yml` はこの CODEOWNERS で守る)。
- OIDC デプロイロールの信頼は `ref:refs/heads/main` 限定。ブランチや fork の実行・fork PR は
  AWS 認証情報を取れない(=外部はデプロイ不可)。書き込み権限者は最小限に保つ。
