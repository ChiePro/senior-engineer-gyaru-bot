"""
AgentCore Memory リソースを「1度だけ」作成する運用スクリプト(デプロイ対象外)。

namespace は slackbot.namespaces を単一ソースとして参照する(strands_runtime と同じ定義)。
実行すると memory_id が出力されるので、それを ECS タスクの環境変数 MEMORY_ID に設定する。

TTL (event_expiry_days):
  短期記憶イベント(生の会話ログ)の保持日数。7〜365 日。用途上問題ない最小値に。
  ※ 長期記憶レコードは抽出後も残るため、肥大化が気になる場合は定期的な整理を別途検討。

使い方:
    pip install bedrock-agentcore
    BEDROCK_REGION=us-east-1 EVENT_EXPIRY_DAYS=30 python -m scripts.create_memory
"""

import os
import sys

from slackbot.namespaces import NS_PREFERENCES, NS_FACTS, NS_SUMMARIES

REGION = os.environ.get("BEDROCK_REGION", "us-east-1")
MEMORY_NAME = os.environ.get("MEMORY_NAME", "SlackBotMemory")
EVENT_EXPIRY_DAYS = int(os.environ.get("EVENT_EXPIRY_DAYS", "30"))

# strategies は namespaces.py の定義をそのまま使う(strands_runtime の retrieval と単一ソース)
STRATEGIES = [
    {
        "userPreferenceMemoryStrategy": {
            "name": "UserPreferences",
            "description": "ユーザーの趣味嗜好・性格・行動パターンを抽出",
            "namespaces": [NS_PREFERENCES],
        }
    },
    {
        "semanticMemoryStrategy": {
            "name": "Facts",
            "description": "ユーザーに関する事実情報を抽出",
            "namespaces": [NS_FACTS],
        }
    },
    {
        "summaryMemoryStrategy": {
            "name": "SessionSummary",
            "description": "スレッド単位の会話要約",
            "namespaces": [NS_SUMMARIES],
        }
    },
]


def find_existing(client) -> "str | None":
    """同名の Memory が既にあれば、その id を返す(二重作成の防止)。"""
    try:
        for m in client.list_memories():
            if m.get("name") == MEMORY_NAME:
                return m.get("id") or m.get("memoryId")
    except Exception:
        pass
    return None


def main() -> int:
    # 重い SDK 依存はここで遅延 import(テストではモジュールを依存なしで読める)
    from bedrock_agentcore.memory import MemoryClient

    client = MemoryClient(region_name=REGION)

    existing = find_existing(client)
    if existing:
        print(f"既に同名の Memory が存在します: {existing}")
        print(f"export MEMORY_ID={existing}")
        return 0

    print(f"Creating AgentCore Memory '{MEMORY_NAME}' (数分かかります)...")
    memory = client.create_memory_and_wait(
        name=MEMORY_NAME,
        description="Slack bot: per-user long-term memory (preferences/facts) + session summaries",
        strategies=STRATEGIES,
        event_expiry_days=EVENT_EXPIRY_DAYS,
    )

    # 返り値のキーは SDK バージョンで 'id' / 'memoryId' の差があるため両対応
    memory_id = memory.get("id") or memory.get("memoryId")

    print("\n=== 作成完了 ===")
    print(f"MEMORY_ID = {memory_id}")
    print(f"TTL(event_expiry_days) = {EVENT_EXPIRY_DAYS} 日")
    print("\nECS タスクの環境変数に設定してください:")
    print(f"export MEMORY_ID={memory_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
