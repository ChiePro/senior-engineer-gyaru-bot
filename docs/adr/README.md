# Architecture Decision Records

このプロジェクトで下した、後から「なぜそうなってるの?」となりがちな決定の記録。
1ファイル1決定。フォーマットは Status / Context / Decision / Consequences。

| # | タイトル | Status |
|---|---|---|
| [0001](0001-ecs-fargate-socket-mode.md) | Lambda をやめ ECS Fargate + Slack Socket Mode にする | Accepted |
| [0002](0002-per-target-user-store.md) | あだ名・特徴・機嫌を「対象者ID」キーの DynamoDB に持つ | Accepted |
| [0003](0003-bedrock-model-selection.md) | 応答モデルに gpt-oss-120b を採用する | Accepted |
| [0004](0004-pure-logic-io-separation.md) | 純粋ロジックと I/O を分離しテストを secret 不要にする | Accepted |
| [0005](0005-single-implementation.md) | DIY/Lambda 二重実装をやめ ECS 単一構成にする | Accepted |
| [0006](0006-cicd-and-branch-protection.md) | SHA イメージ + SSM secret + OIDC CI/CD + main 保護 | Accepted |
| [0007](0007-cost-change-approval-gate.md) | お金に関わる変更(ecs.yaml)のデプロイは所有者承認を必須にする | Accepted |
| [0008](0008-thread-autoreply.md) | メンション済みスレッドではメンション無しでも自発応答する | Accepted |

新しい決定は連番を振って追加する。過去の ADR を覆すときは古い方の Status を
`Superseded by ADR-XXXX` にし、新しい ADR の Context に経緯を書く。
