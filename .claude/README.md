# .claude

このプロジェクトで作業するときの「従うべき規約」と「具体的な作業手順」。
設計の why は [`docs/`](../docs/README.md)、ここは作業時の how。

## rules/ — 常に意識する規約

| ファイル | 内容 |
|---|---|
| [architecture.md](rules/architecture.md) | 純粋/I/O 分離・単一ソース・三層メモリの不変条件(最重要) |
| [persona.md](rules/persona.md) | ギャル口調の不変条件(タメ口基調・正確さ・タグ非出力・保存非宣言・塩対応) |
| [deployment.md](rules/deployment.md) | ECS/Socket Mode の前提・CI/CD・secret 運用・ハマりどころ |
| [testing.md](rules/testing.md) | secret 不要のテスト境界・何を検査するか・実モデル検証 |

## skills/ — タスク別の手順書(該当作業時に発動)

| スキル | いつ使う |
|---|---|
| [switch-bedrock-model](skills/switch-bedrock-model/SKILL.md) | 応答モデルを変える / モデルの可否を調べる |
| [tune-persona](skills/tune-persona/SKILL.md) | 口調・性格・ふるまいを調整して実モデルで検証する |
| [deploy-and-rollback](skills/deploy-and-rollback/SKILL.md) | CI 外で手動デプロイ / 稼働確認 / ロールバック |
| [agentcore-memory-ops](skills/agentcore-memory-ops/SKILL.md) | 記憶の作成・覗き・リセット・ゴミ掃除 |

rules は「いつも守る前提」、skills は「特定の作業をするときに開く手順」。
新しい知見が溜まったら、該当する rule/skill を更新するか、新規に足す。
