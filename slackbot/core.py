"""
I/O を持たない純粋ロジック。boto3 / slack / strands に依存しないので、
重い依存をインストールせずに単体テストできる。

Socket Mode 版 (socket_app.py) と応答生成 (strands_runtime.py) がこのモジュールの
メンション整形・人物注記・内部タグ除去・ID 整形を使う。外部 I/O (Slack/Bedrock/DynamoDB)
は呼び出し側に置き、ここには持ち込まない。
"""

import re
from typing import Optional

_MENTION_ID_RE = re.compile(r"<@([A-Za-z0-9]+)(?:\|[^>]*)?>")


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
