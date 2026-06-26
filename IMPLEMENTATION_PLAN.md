# 実装計画: Strands 版を ECS Fargate + Socket Mode へ移行

Lambda のコールドスタートが Slack の3秒ルールに勝てず、Provisioned Concurrency も
アカウント同時実行上限(10)で確保不可だったため、常時起動の ECS Fargate + Slack Socket Mode
へ移行する。Socket Mode により公開エンドポイント / API Gateway / URL 検証 / 自己 invoke が不要になる。

## Stage 1: アプリを Socket Mode 化
**Goal**: 常駐プロセスとして Slack へ outbound WebSocket を張り、メンションに応答する
**成果物**: `slackbot/strands_runtime.py`(応答生成の共有関数)、`slackbot/socket_app.py`(常駐エントリ)
**Status**: Complete

## Stage 2: Fargate 用コンテナ
**Goal**: Lambda ベースではない通常の Python イメージで socket_app を起動
**成果物**: `Dockerfile.fargate`、`requirements_strands.txt` に websocket-client 追加
**Status**: Complete

## Stage 3: ECS インフラ(CloudFormation)
**Goal**: クラスタ + Fargate サービス(常時1) + IAM + SG + ログ。ALB/NAT なし、default VPC public subnet + assignPublicIp
**成果物**: `ecs.yaml`、シークレットは SSM Parameter Store(SecureString)参照
**Status**: Complete

## Stage 4: Slack 設定 + ローカル初回デプロイ
**Goal**: Socket Mode 有効化 + app-level token(xapp-)発行、SSM 投入、ECR push、スタック作成、動作確認
**Status**: Complete

## Stage 5: CI/CD を ECS 用に作り替え
**Goal**: deploy.yml を「image build→ECR push→cloudformation deploy ecs.yaml」に変更、OIDC ロールに ecs/ec2/ssm 権限追加
**Status**: Not Started

## 後片付け
- 旧 SAM(Lambda)スタック `senior-engineer-gyaru-bot` は不要になったら削除
