"""
namespace の単一ソース。scripts/create_memory.py と slackbot/app_strands.py の両方が
これを参照することで、登録側と検索側の namespace 食い違い(長期記憶がヒットしない不具合)を
構造的に防ぐ。
"""

# AgentCore に登録する namespace テンプレート。{actorId}/{sessionId} は AgentCore が
# 実行時に置換する literal プレースホルダ。末尾スラッシュは AWS 推奨(プレフィックス衝突防止)。
NS_PREFERENCES = "/users/{actorId}/preferences/"
NS_FACTS = "/users/{actorId}/facts/"
NS_SUMMARIES = "/users/{actorId}/summaries/{sessionId}/"


def resolve(template: str, actor_id: str, session_id: str | None = None) -> str:
    """テンプレート中の {actorId} / {sessionId} を実値へ置換する。
    app 側の retrieval_config は AgentCore のテンプレートではなく実値の namespace を渡すため、
    ここで同じ文字列を組み立てる。
    """
    out = template.replace("{actorId}", actor_id)
    if session_id is not None:
        out = out.replace("{sessionId}", session_id)
    return out
