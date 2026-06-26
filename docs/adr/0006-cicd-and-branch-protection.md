# ADR-0006: SHA イメージ + SSM secret + OIDC CI/CD + main 保護

- Status: Accepted
- Date: 2026-06-26

## Context

ECS 移行後、自動デプロイの仕組みと、本番への変更経路の安全性を決める必要があった。考慮点:

- イメージタグを `:latest` にすると CloudFormation が ImageUri 変化を検知せずローリングしない。
- Slack トークンを CI に渡すと、GitHub Actions のログ/設定に secret が広がる。
- リポジトリは **PUBLIC** で、書き込み権限者が7名(うち admin 4名)。`main` に保護が無く、
  **誰でも直 push でき、それが即本番デプロイされる**状態だった(タスクロールは Bedrock/DynamoDB/
  AgentCore 権限を持つため、任意コードの本番反映は実害が大きい)。

## Decision

1. **イメージタグは commit SHA。** ImageUri が毎回変わり TaskDefinition 更新 → ECS が自動ローリング。
2. **Slack トークンは SSM Parameter Store(SecureString)。** CI からは渡さず、ECS 実行ロールが
   起動時に解決する。CI が触る secret は `MEMORY_ID` のみ。
3. **デプロイ認証は GitHub OIDC。** `infra/github-oidc-bootstrap.yaml` の `ecs-container-deploy`
   ポリシー(cloudformation/ecr/ecs/ec2-SG/dynamodb/logs、IAM 管理は `gyaru-bot-ecs-*` 限定、
   PassRole は `ecs-tasks.amazonaws.com`)。長期アクセスキーを GitHub に置かない。
4. **`main` ブランチ保護**(ruleset 相当, `enforce_admins=true`): PR 必須(直 push 禁止)/承認1件以上/
   CI(`test` ジョブ)通過必須/会話解決必須/force push・削除禁止。

## Consequences

- 利点: 本番へは「PR → レビュー承認 + CI green → マージ」の経路だけが届く。secret は AWS 側に集約。
  ロールバックは過去 SHA タグへ deploy し直すだけ。
- トレードオフ: `enforce_admins=true` により owner を含め**全員が PR 必須**(小修正もブランチ→PR)。
  緩めることは可能だが、その分「直 push → 即デプロイ」のリスクが戻る。
- 残課題: リポジトリは PUBLIC のまま(secret はコードに無いので必須対応ではない)。露出した
  トークンのローテーションは別途実施する運用([.claude/rules/deployment.md](../../.claude/rules/deployment.md))。
- フロー詳細は README「CI/CD」、操作は `deploy-and-rollback` スキル。
