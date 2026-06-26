"""
I/O を持たない純粋ロジック。boto3 / slack / strands に依存しないので、
重い依存をインストールせずに単体テストできる。

DIY 版 (app.py) はこのモジュールを使って会話整形・記憶注入・制御フローを行い、
外部 I/O (Slack/Bedrock/DynamoDB) は呼び出し側から callable として注入する。
"""

import re
from dataclasses import dataclass
from typing import Callable, Optional

_MENTION_RE = re.compile(r"<@[\w]+>")


def clean_mention(text: str) -> str:
    """ボットメンション (<@U123...>) を除去し前後空白を削る。"""
    return _MENTION_RE.sub("", text or "").strip()


def is_slack_retry(event: dict) -> bool:
    """Slack のリトライ(タイムアウト再送)かどうか。二重応答防止用。"""
    return bool((event.get("headers") or {}).get("x-slack-retry-num"))


def build_conversation(replies: list, bot_user_id: str, max_history: int = 20) -> list:
    """スレッドの発言を Bedrock Converse 用の messages に変換する。

    Converse の制約に合わせ:
      - subtype 付き(参加通知等)は除外
      - 空テキストは除外
      - bot 自身の発言は assistant、それ以外は user
      - 直近 max_history 件に制限
      - 連続する同一 role は結合(role 交互が必須)
      - 先頭の assistant は捨てて user 始まりにする
    """
    raw = []
    for m in replies:
        if m.get("subtype"):
            continue
        text = clean_mention(m.get("text", ""))
        if not text:
            continue
        role = "assistant" if m.get("user") == bot_user_id else "user"
        raw.append((role, text))

    raw = raw[-max_history:]

    merged = []
    for role, text in raw:
        if merged and merged[-1][0] == role:
            merged[-1] = (role, merged[-1][1] + "\n" + text)
        else:
            merged.append((role, text))

    while merged and merged[0][0] == "assistant":
        merged.pop(0)

    messages = [{"role": r, "content": [{"text": t}]} for r, t in merged]
    if not messages:
        messages = [{"role": "user", "content": [{"text": "こんにちは"}]}]
    return messages


def build_system_prompt(base_prompt: str, memory: str) -> str:
    """長期記憶を system prompt に注入する。memory が空なら base のみ。"""
    if not memory:
        return base_prompt
    return (
        f"{base_prompt}\n\n"
        "以下はこのユーザーについて過去のやり取りから分かっている長期的な情報です。"
        "応答の参考にし、自然に活かしてください(無理に全部使う必要はありません):\n"
        f"{memory}"
    )


@dataclass
class TurnResult:
    reply: str
    thread_ts: str
    user_id: Optional[str]
    memory: str
    current_text: str


def prepare_reply(
    *,
    event: dict,
    bot_user_id: str,
    base_prompt: str,
    max_history: int,
    fetch_thread: Callable[[str, str], list],
    load_memory: Callable[[str], str],
    generate_reply: Callable[[list, str], str],
) -> TurnResult:
    """1ターンの応答生成までの純粋な制御フロー(投稿はしない)。
    外部 I/O は callable として注入する。
    """
    user_id = event.get("user")
    thread_ts = event.get("thread_ts") or event.get("ts")
    channel = event["channel"]

    memory = load_memory(user_id) if user_id else ""
    replies = fetch_thread(channel, thread_ts)
    messages = build_conversation(replies, bot_user_id, max_history)
    system_prompt = build_system_prompt(base_prompt, memory)
    reply = generate_reply(messages, system_prompt)

    return TurnResult(
        reply=reply,
        thread_ts=thread_ts,
        user_id=user_id,
        memory=memory,
        current_text=clean_mention(event.get("text", "")),
    )


def update_long_term_memory(
    result: TurnResult,
    *,
    extract_memory: Callable[[str, str], str],
    save_memory: Callable[[str, str], None],
) -> Optional[str]:
    """返信後に呼ぶ長期記憶の更新。抽出結果が空なら何もしない。"""
    if result.user_id and result.current_text:
        new_mem = extract_memory(result.memory, result.current_text)
        if new_mem:
            save_memory(result.user_id, new_mem)
            return new_mem
    return None
