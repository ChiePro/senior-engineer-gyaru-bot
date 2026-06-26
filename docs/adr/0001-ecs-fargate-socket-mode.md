# ADR-0001: Lambda をやめ ECS Fargate + Slack Socket Mode にする

- Status: Accepted
- Date: 2026-06-26

## Context

当初は API Gateway + Lambda(イベント受信 → Bedrock 応答)で構築した。しかし:

- Slack の **3秒ルール**(イベント ack / URL 検証)に、重い依存(`strands-agents` /
  `bedrock-agentcore`)を抱えた Lambda の **INIT(コールドスタート)が間に合わない**。
- 回避策の Lazy-listener(自分を非同期 invoke)は配線が複雑で、実行ロールに
  `lambda:InvokeFunction`(自己)が要る等、事故りやすい。
- 本命の **Provisioned Concurrency** は、アカウントの Lambda 同時実行上限が既定の `10` のままだと
  「未予約を最低10残す」制約に当たり**確保できなかった**(上限引き上げ申請が必要)。

「とにかく早く安定動作させたい」が優先で、Lambda の制約に時間を取られ続けるのは非効率だった。

## Decision

**ECS Fargate に常駐コンテナを1タスク置き、Slack Socket Mode(outbound WebSocket)で接続する。**

- 受信用の公開 URL が不要 → API Gateway・URL 検証・署名検証・自己 invoke がすべて消える。
- 常駐なのでコールドスタートが無く、3秒ルールに常に間に合う。
- Socket Mode は 1 接続 = 1 タスクなので `desiredCount=1`、デプロイは旧タスク停止 → 新タスク起動
  (`MinimumHealthyPercent: 0`)で二重接続を避ける。
- ネットワークは default VPC の public subnet + `assignPublicIp` で outbound するだけ(ALB/NAT 不要)。

## Consequences

- 利点: コールドスタート/3秒ルール/URL 検証の問題が**構造的に消える**。配線が単純化。
- コスト: Lambda の従量(ほぼゼロ)から、Fargate の常時起動コスト(0.5vCPU/1GB 常駐)に変わる。
- 運用: App-Level Token(`xapp-`, `connections:write`)が必要。ローリング中は数十秒〜1分ほど無応答。
- 関連: [ADR-0005](0005-single-implementation.md)(Lambda 実装の撤去)、[ADR-0006](0006-cicd-and-branch-protection.md)(ECS 用 CI/CD)。
- 将来: トラフィックが増えても 1 接続前提は変わらないので、水平スケールするなら Socket Mode の
  多重接続戦略かイベント API への再設計が要る(現状は不要)。
