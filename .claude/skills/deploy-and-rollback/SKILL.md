---
name: deploy-and-rollback
description: Manually build, push, deploy, verify, or roll back the gyaru bot on ECS Fargate. Use when the user wants to deploy outside the CI/CD pipeline, check the running task/image, tail logs, or revert to a previous image. Covers the linux/amd64 and zsh tag-mangling gotchas.
---

# ECS への手動デプロイ / ロールバック

本番は `us-east-1` の CloudFormation スタック `gyaru-bot-ecs`、ECS サービス `gyaru-bot-ecs`、
ECR リポジトリ `gyaru-bot`。ローカル操作は **`AWS_PROFILE=gyaru-admin`**。

> 通常は `main` へのマージで CI/CD が回るのでこの手順は不要。緊急時/CI 外の検証用。

## ビルド & push(ハマりどころ2つ)

```bash
export AWS_PROFILE=gyaru-admin
REPO=772058221854.dkr.ecr.us-east-1.amazonaws.com/gyaru-bot
TAG=$(git rev-parse HEAD)                      # latest ではなく SHA タグにする
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin "${REPO%/*}"
docker build --platform linux/amd64 -f Dockerfile.fargate -t "${REPO}:${TAG}" .   # ← amd64 必須
docker push "${REPO}:${TAG}"
```

- **`--platform linux/amd64` 必須**(arm64 Mac の素ビルドは Fargate で起動しない)。
- **zsh はタグを波括弧で**: `"${REPO}:${TAG}"`。`$REPO:latest` は `:l` 修飾子で `gyaru-botatest` に化ける。

## デプロイ(CloudFormation)

`ImageUri` を新タグにして deploy すると TaskDefinition が変わり自動ローリングする。

```bash
aws cloudformation deploy --region us-east-1 \
  --template-file ecs.yaml --stack-name gyaru-bot-ecs --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
    "ImageUri=${REPO}:${TAG}" \
    "MemoryId=SlackBotMemory-uefTsX9os8" \
    "BedrockRegion=us-east-1" "BedrockModelId=openai.gpt-oss-120b-1:0" \
    "VpcId=vpc-0fd83370fb8795d3a" \
    "SubnetIds=subnet-0e6cc950fc80fe26c,subnet-086d62717df567935,subnet-0b892b3f09da9c3e0"
aws ecs wait services-stable --cluster gyaru-bot-ecs --services gyaru-bot-ecs --region us-east-1
```

`VpcId`/`SubnetIds`/`MemoryId` は no-default パラメータなので毎回渡す(現行値は上記)。

## 環境変数だけ変えて再起動(コード変更なし)

```bash
# 例: SSM のトークンを更新した後など。同じイメージで作り直す。
aws ecs update-service --cluster gyaru-bot-ecs --service gyaru-bot-ecs \
  --force-new-deployment --region us-east-1
```

## 稼働確認

```bash
TASK=$(aws ecs list-tasks --cluster gyaru-bot-ecs --service-name gyaru-bot-ecs --region us-east-1 --query 'taskArns[0]' --output text)
aws ecs describe-tasks --cluster gyaru-bot-ecs --tasks "$TASK" --region us-east-1 --query 'tasks[0].containers[0].image' --output text
aws ecs describe-services --cluster gyaru-bot-ecs --services gyaru-bot-ecs --region us-east-1 \
  --query 'services[0].{Running:runningCount,Desired:desiredCount,Deployments:length(deployments)}'
aws logs tail /ecs/gyaru-bot-ecs --region us-east-1 --since 5m --format short | grep -iE "Bolt|established|Traceback|Error"
```
`⚡️ Bolt app is running!` が出れば Slack 接続 OK。

## ロールバック

過去イメージのタグ(=過去 commit SHA)へ `aws cloudformation deploy` し直すだけ。

```bash
aws ecr describe-images --repository-name gyaru-bot --region us-east-1 \
  --query 'sort_by(imageDetails,&imagePushedAt)[].imageTags' --output table   # 候補タグを見る
# 戻したい SHA を ImageUri に入れて上の deploy を再実行
```
