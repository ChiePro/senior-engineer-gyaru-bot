# IMPLEMENTATION_PLAN: Bot に Web 検索(最新情報)を持たせる

## ゴール / 方針

ボットが「最新性が要る質問(バージョン・ニュース・現在の状況など)」のときだけ Web を検索し、
結果を**要約して・口調はギャルのまま・盛らずに**返せるようにする。

- 検索は **Tavily**(LLMエージェント特化の検索API)。`strands_runtime._build_tools` に
  function-calling ツール `web_search` を1個足す既存パターンに乗せる。
- **I/O境界を壊さない**: ネットワーク呼び出しは I/O 層(`strands_runtime`)に置く。検索結果の整形・
  発動文言などの純粋ロジックは `core.py` に置いて TDD で検査する。`core/persona/namespaces` に
  Tavily / 環境変数を持ち込まない。
- **戻り値は短く**保つ(既存ツールの方針)。生の検索ペイロードをそのまま渡さず、上位N件を
  「タイトル — 1〜2文要約 (URL)」に整形して総文字数を上限で切る。
- **コスト** は「最新性が要るときだけ検索」で抑える。Tavily 無料枠 月1000検索 → 以降従量。
- **新 secret = `ecs.yaml` 変更 = コスト承認ゲート**に乗る(外部有料API＝お金に関わる変更なので妥当)。

## 未決事項(着手前に確定 or 進めながら判断)

- 検索の `search_depth`(basic / advanced)と `max_results`。まず basic + max_results=5 で開始。
- `web_search` の発動条件をプロンプトでどこまで縛るか(毎回検索させない)。Stage 2 で文言調整。
- 純正 `strands_tools.tavily` ではなく**薄い自前ツール**を採用(戻り値整形を自分で制御するため)。

---

## Stage 1: 検索結果の整形(純粋ロジック・TDD)
**Goal**: `core.format_search_results(results, *, max_items=5, max_chars=1200) -> str` を追加。
  Tavily の results(list[dict: title/url/content])を短い注記文字列に整形する。空なら ""。
**Success Criteria**:
  - 上位 max_items 件だけ。各件「- タイトル — 要約 (URL)」形式。
  - 総文字数が max_chars を超えたら件数を削って収める(URLは壊さない)。
  - title/content 欠落や空 list に耐える(I/O を持たない・例外を投げない)。
**Tests** (`tests/test_unit.py`):
  - 空 list → ""。
  - 3件 → 各タイトル/URL が含まれる、件数が max_items 以下。
  - max_chars 超過 → 切り詰めても先頭件は残り URL が途切れない。
**Status**: Complete

## Stage 2: `web_search` ツールの配線(I/O)
**Goal**: `strands_runtime` に `@tool web_search(query: str) -> str` を追加し、Tavily を叩いて
  `core.format_search_results` で整形して返す。`_build_tools` の返却リストに加える。
**Success Criteria**:
  - `tavily-python` を `requirements.txt`(本番) に追加、`Dockerfile.fargate` のビルドで入る。
    `requirements-dev.txt` には足さない(テストは core の整形だけ検査するので Tavily 不要)。
  - API キーは `os.environ["TAVILY_API_KEY"]`(I/O 層でのみ読む)。未設定なら `web_search` を
    ツールに**入れない**(キー無し環境＝CI/ローカルで壊れない。`compileall` は実行しないので元々安全)。
  - `persona.BEHAVIOR_GUIDE` に検索の使い方を追記:「最新性・確実性が要るとき**だけ**検索/結果は
    要約して口調はギャルのまま/ソースを踏まえて盛らない/毎回は検索しない」。
  - 戻り値は短く(Stage 1 の整形済み文字列)。検索失敗時は短い日本語メッセージを返しループさせない。
**Tests**:
  - `tests/test_persona.py`: BEHAVIOR_GUIDE に検索関連の文言/ツール名 `web_search` が含まれること。
  - ツール本体(I/O)はユニット対象外 → Stage 4 の実モデル検証で担保。
**Status**: Not Started

## Stage 3: secret & インフラ(`ecs.yaml` / SSM)
**Goal**: Tavily API キーを SSM(SecureString)に置き、タスクに注入する。Slack トークンと同パターン。
**Success Criteria**:
  - SSM: `aws ssm put-parameter --name /gyaru-bot/TAVILY_API_KEY --type SecureString ...`(手動・1回)。
  - `ecs.yaml` を3箇所更新:
    1. `Parameters` に `TavilyApiKeyParam`(Default `/gyaru-bot/TAVILY_API_KEY`)。
    2. `ExecutionRole` の `read-secrets` の Resource に Tavily param ARN を**明示追加**(現状ワイルドカードではない)。
    3. コンテナ `Secrets` に `TAVILY_API_KEY` → `ValueFrom` を追加。
  - `cfn-lint ecs.yaml` が通る。
  - **これで `ecs.yaml` が差分に出る → デプロイ時に所有者のコスト承認ゲートを通る**(設計どおり)。
**Tests**: `cfn-lint ecs.yaml infra/github-oidc-bootstrap.yaml`。
**Status**: Not Started

## Stage 4: 実モデル検証(ユニットの外)
**Goal**: 一時 venv に `boto3 / strands-agents / tavily-python` を入れ、`AWS_PROFILE=gyaru-admin` +
  `TAVILY_API_KEY` で、最新性が要る質問を数問投げて `str(agent(...))` を目視(scratchpad、リポジトリに残さない)。
**Success Criteria**:
  - 「最新の○○のバージョンは?」等で `web_search` が発動し、要約して答える(口調はギャル維持)。
  - 雑談・自明な質問では検索しない(無駄打ちしない)。
  - 内部思考タグが漏れない(`strip_internal_tags` 込みで確認)。
**Tests**: 手動目視(`tune-persona` / `switch-bedrock-model` スキルの実モデル検証手順に準拠)。
**Status**: Not Started

## Stage 5: デプロイ(PR → 承認 → 本番)
**Goal**: PR を作り、コスト承認ゲート(`ecs.yaml` 差分)を通して本番反映。
**Success Criteria**:
  - `pytest` / `ruff` / `compileall` / `cfn-lint` green。
  - PR マージ → `deploy.yml` の detect が `ecs.yaml` 差分を検知 → approval(所有者承認) → deploy →
    `ecs wait services-stable`。
  - Slack で最新情報系の質問をして実機確認。
  - 完了後この `IMPLEMENTATION_PLAN.md` を削除。
**Status**: Not Started

---

## ドキュメント更新(完了時)
- `CLAUDE.md` / `.claude/rules/architecture.md`: I/O 層に `web_search`(Tavily)を足したこと、
  キーは SSM、整形は `core.format_search_results` に分離、を1行ずつ追記。
- `docs/design/` に簡単な追記 or ADR(検索プロバイダ選定の経緯)を検討。
