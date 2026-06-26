# ADR-0007: お金に関わる変更のデプロイは所有者承認を必須にする

- Status: Accepted
- Date: 2026-06-26

## Context

[ADR-0006](0006-cicd-and-branch-protection.md) で `main` へのマージ = 自動本番デプロイになった。
通常のコード/ドキュメント変更はこれで良いが、**継続課金や単価に直結する変更**(Fargate のタスク
サイズ・タスク数、DynamoDB 課金モード、Bedrock モデルの単価)が、レビュー承認だけで気づかぬまま
本番反映され、コストが跳ねるリスクがあった。これらは「マージしてよいか」とは別に「**お金を使って
よいか**」の判断が要る。

課題: GitHub の environment 承認は「ジョブ単位で常にゲート」しかできず、「特定の変更のときだけ」を
ネイティブには表現できない。また Bedrock モデルは当時 GitHub 変数 `BEDROCK_MODEL_ID` 経由で、
コミット差分に現れず検出できなかった。

## Decision

**「お金に関わる変更」を `ecs.yaml` への変更と定義し、それが含まれるデプロイだけ所有者承認を必須にする。**

- コストに関わるパラメータ(Cpu/Memory/DesiredCount/DynamoDB 課金モード/**BedrockModelId**)は
  すべて `ecs.yaml` で管理する。**モデル ID の単一ソースを GitHub 変数から `ecs.yaml` の Default に移し**、
  `deploy.yml` は `BedrockModelId` を override しない → モデル変更も `ecs.yaml` の差分として検出される。
- `deploy.yml` を3ジョブ化: **detect**(`git diff HEAD^ HEAD` に `ecs.yaml` が含まれるか)→
  **approval**(`cost_change` のときだけ、required reviewers 付き environment `production` を通る空ジョブ)→
  **deploy**(detect 成功 かつ approval が成功/スキップのときのみ実行)。
- environment `production` の required reviewer = リポジトリ所有者。bypass モードではなく
  「ゲートを通る」方式なので、所有者が Approve するまで deploy は止まる。

## Consequences

- 利点: タスクサイズ・台数・課金モード・モデル単価の変更は、**デプロイ直前に所有者が明示承認**しないと
  本番に出ない。docs やアプリコードだけの変更は従来どおり自動デプロイ(摩擦ゼロ)。
- トレードオフ: モデル変更が「GitHub 変数を1つ変える」から「`ecs.yaml` を PR で変える」に増える。
  ただしそれが承認ゲートに乗せる前提条件でもある。
- 運用前提: GitHub に `production` environment と required reviewers を設定しておくこと(未設定だと
  承認ステップが素通りする)。
- 検出範囲の限界: 「お金に関わる変更」= `ecs.yaml` 差分というプロキシ。AWS 側でコンソール直変更したり、
  `ecs.yaml` を介さないコスト要因(例: 別リージョン手動操作)は検出対象外。必要なら検出ファイルを増やす。
- **ゲートの自己防衛**: 承認ゲートは `deploy.yml` 内にあり、それ自体は「コスト変更」と判定されないため、
  ゲートを書き換える PR は通常フロー(任意の1承認)で通ってしまう穴がある。これを塞ぐため
  `.github/CODEOWNERS` で `.github/`・`ecs.yaml`・`infra/` を所有者所有にし、ruleset の
  "require code owner review" を有効化した。`.github/CODEOWNERS` 自身も `.github/` 配下なので、
  CODEOWNERS を書き換える PR も所有者承認が要る(自己保護)。Org Owner は ruleset を bypass できる
  (設計どおり信頼前提)。
- 補助的防御: OIDC ロールの信頼は `ref:refs/heads/main` 限定で、ブランチ/fork 実行や fork PR は
  AWS 認証情報を取得できない(外部からのデプロイ・secret 流出を構造的に防ぐ)。
- 関連: [.claude/rules/deployment.md](../../.claude/rules/deployment.md) / `switch-bedrock-model` スキル。
