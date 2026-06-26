# docs

このプロジェクトの設計ドキュメントと意思決定記録。

- **[adr/](adr/README.md)** — Architecture Decision Records。後から「なぜこうなってるの?」と
  なりがちな決定を1ファイル1件で記録(Status / Context / Decision / Consequences)。
- **[design/](design/)** — 設計ドキュメント。
  - [architecture.md](design/architecture.md) — システム全体像・モジュール責務・処理フロー。
  - [memory-model.md](design/memory-model.md) — 三層メモリ(短期 / 長期 / 人物属性)の設計。

実装規約・運用ノウハウは [`.claude/`](../.claude/README.md)(rules / skills)側にある。
docs は「設計と決定の why」、`.claude` は「作業時に従う how」と役割分担している。
