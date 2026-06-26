"""
I/O を持たない純粋ロジック。boto3 / slack / strands に依存しないので、
重い依存をインストールせずに単体テストできる。

DIY 版 (app.py) はこのモジュールを使って会話整形・記憶注入・制御フローを行い、
外部 I/O (Slack/Bedrock/DynamoDB) は呼び出し側から callable として注入する。
"""

import re
from dataclasses import dataclass
from typing import Callable, Optional

_MENTION_RE = re.compile(r"<@[\w]+(?:\|[^>]*)?>")
_MENTION_ID_RE = re.compile(r"<@([A-Za-z0-9]+)(?:\|[^>]*)?>")


def clean_mention(text: str) -> str:
    """すべてのメンション (<@U123...>) を除去し前後空白を削る。

    注意: 第三者へのメンションも消えるため「誰の話か」が失われる。あだ名のように
    対象人物を保持したい用途では strip_bot_mention + mentioned_user_ids を使う。
    """
    return _MENTION_RE.sub("", text or "").strip()


def strip_bot_mention(text: str, bot_user_id: str) -> str:
    """ボット自身へのメンション <@bot> だけ除去し、他者メンションは <@Uxxx> のまま残す。

    これにより「@bot @坂本 は坂もっちゃん」→「<@坂本> は坂もっちゃん」となり、
    モデルが「誰のあだ名か」を ID で判別できる(発言者との取り違えを防ぐ)。
    """
    text = text or ""
    if bot_user_id:
        text = re.sub(rf"<@{re.escape(bot_user_id)}(?:\|[^>]*)?>", "", text)
    return text.strip()


def mentioned_user_ids(text: str, exclude: Optional[str] = None) -> list:
    """本文中の <@Uxxx> のユーザーIDを、順序保持・重複排除で返す。exclude は除く。"""
    ids = []
    for m in _MENTION_ID_RE.finditer(text or ""):
        uid = m.group(1)
        if uid != exclude and uid not in ids:
            ids.append(uid)
    return ids


_SLACK_ID_RE = re.compile(r"[UW][A-Z0-9]{2,}")


def normalize_slack_id(value: str) -> Optional[str]:
    """文字列から Slack ユーザーID を取り出す。

    モデルが <@U123>, <@U123|name>, "U123", さらには "</user_id>" のような壊れた値を
    ツール引数に渡すことがあるため、正規の ID 部分だけ抽出する。取れなければ None。
    """
    m = _SLACK_ID_RE.search(value or "")
    return m.group(0) if m else None


def build_people_note(profiles: dict, speaker_id: str) -> str:
    """登場人物のワークスペース共有プロフィール(あだ名・特徴)を system prompt 用の注記にする。

    profiles: {user_id: {"nickname": str|None, "notes": list[str]}}
    あだ名も特徴も無い人は出さない。空なら空文字。
    """
    lines = []
    for uid, p in (profiles or {}).items():
        nickname = (p or {}).get("nickname")
        notes = (p or {}).get("notes") or []
        if not nickname and not notes:
            continue
        head = f"- {uid}"
        if nickname:
            head += f" = {nickname}"
        if uid == speaker_id:
            head += "(発言者本人)"
        if notes:
            head += " | " + "; ".join(notes)
        lines.append(head)
    if not lines:
        return ""
    return "登場人物(ワークスペース共有の情報):\n" + "\n".join(lines)


def is_slack_retry(event: dict) -> bool:
    """Slack のリトライ(タイムアウト再送)かどうか。二重応答防止用。"""
    return bool((event.get("headers") or {}).get("x-slack-retry-num"))


_INTERNAL_BLOCK_RE = re.compile(
    r"<(thinking|reasoning|scratchpad|reflection)>.*?</\1>", re.DOTALL | re.IGNORECASE
)
_INTERNAL_LONE_RE = re.compile(r"</?(thinking|reasoning|scratchpad|reflection)>", re.IGNORECASE)


def strip_internal_tags(text: str) -> str:
    """モデルが本文に混ぜる内部思考タグ(<thinking>...</thinking> 等)を除去する。

    プロンプトで禁止しても漏れることがあるため、Slack へ出す前にここで確実に落とす。
    対応外の閉じ忘れタグも単独タグとして除去し、前後の空白を整える。
    """
    t = _INTERNAL_BLOCK_RE.sub("", text or "")
    t = _INTERNAL_LONE_RE.sub("", t)
    return t.strip()


_ID_INVALID_RE = re.compile(r"[^a-zA-Z0-9_-]")


def safe_id(value: str) -> str:
    """AgentCore の actorId/sessionId 制約 [a-zA-Z0-9][a-zA-Z0-9-_]* に収まるよう整形する。

    Slack の thread_ts は '1719374400.123456' のようにドットを含み、そのままでは
    sessionId として弾かれるため、許可外の文字を '-' に置換する。先頭は英数字にする。
    同じ入力は常に同じ出力になるので、スレッド単位の短期記憶の紐付けは保たれる。
    """
    cleaned = _ID_INVALID_RE.sub("-", value or "")
    if not cleaned or not cleaned[0].isalnum():
        cleaned = "s" + cleaned
    return cleaned


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
