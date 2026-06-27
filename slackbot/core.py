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


def build_nickname_directory(nicknames: dict) -> str:
    """全ユーザーのあだ名(呼び名)一覧を system prompt 用の注記にする。

    nicknames: {user_id: nickname}
    build_people_note が「会話の登場人物」だけを注入するのに対し、こちらは会話に未登場の人も
    含めた全件のあだ名を入れる。ユーザーがあだ名で人を呼んだとき(例:@メンション無しで
    「さかもっちゃん元気?」)に、それが誰のことか必ず照合できる状態を保つため。あだ名は件数が
    少なく価値が高いので、関連度フィルタは掛けず常に全件渡す。あだ名が無い人は出さない。
    """
    lines = [f"- {uid} = {nn}" for uid, nn in (nicknames or {}).items() if nn]
    if not lines:
        return ""
    return (
        "あだ名辞書(この呼び名はこの人。あだ名で呼ばれたら誰のことか必ずここで照合する):\n"
        + "\n".join(lines)
    )


def format_search_results(results, *, max_items: int = 5, max_chars: int = 1200) -> str:
    """Web 検索結果を、モデルに渡す短い注記文字列へ整形する(I/O は持たない)。

    results: [{"title": str, "url": str, "content": str}, ...](Tavily の results 形式)
    1件 = 行頭 "- " の1行「- タイトル — 要約 (URL)」。要約は content を短く切る。
    生の検索ペイロードをそのまま渡すと context とコストが膨らむため、上位 max_items 件に絞り、
    総文字数が max_chars を超える場合は末尾の件から落として収める(載せる URL は途中で切らない)。
    title/content 欠落や空入力に耐え、例外は投げない。
    """
    lines = []
    for r in (results or [])[:max_items]:
        if not isinstance(r, dict):
            continue
        url = (r.get("url") or "").strip()
        title = (r.get("title") or "").strip() or url or "(無題)"
        content = " ".join((r.get("content") or "").split())  # 改行・連続空白をならす
        summary = content[:120] + ("…" if len(content) > 120 else "")
        line = f"- {title}"
        if summary:
            line += f" — {summary}"
        if url:
            line += f" ({url})"
        lines.append(line)
    # max_chars を超えない範囲で、先頭の件から詰められるだけ詰める(行単位で落とす=URL を割らない)。
    out = ""
    for line in lines:
        candidate = line if not out else out + "\n" + line
        if len(candidate) > max_chars:
            break
        out = candidate
    return out


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


# --- スレッド内メンションレス応答の判定ロジック(純粋) ---
# Slack/SDK を持ち込まず、socket_app から渡された素の値・dict だけで判定する。

# グループスレッドで「今は割り込まない」とモデルが決めたとき、本文の代わりに返す印。
# 検出(is_silent_reply)と人格ガイド(persona.GROUP_REPLY_GUIDE)が同じ綴りを使う(test で一致を検査)。
SKIP_TOKEN = "<skip/>"
_SKIP_RE = re.compile(r"</?\s*skip\s*/?>", re.IGNORECASE)


def is_autoreply_candidate(
    *, is_thread_reply: bool, is_from_bot: bool, has_subtype: bool, mentions_bot: bool
) -> bool:
    """conversations.replies を叩く前の安いフィルタ。メンションレス応答の候補かを返す。

    - is_thread_reply: スレッドへの返信か(トップレベル発言は対象外)
    - is_from_bot: 自分(Bot)の発言・bot_message か(自己応答ループ防止)
    - has_subtype: 編集・参加通知など subtype 付きか(通常の人間発言だけ拾う)
    - mentions_bot: この発言が Bot をメンションしているか(その場合は app_mention が処理)
    """
    return is_thread_reply and not is_from_bot and not has_subtype and not mentions_bot


def summarize_thread(messages, bot_user_id: str, *, max_transcript: int = 12):
    """conversations.replies の messages から (bot_mentioned, human_ids, transcript) を作る。

    - bot_mentioned: スレッドのどこかで Bot がメンションされたことがあるか(=反応してよいスレッドか)
    - human_ids: 実際に書き込んだ人間のID(順序保持・重複排除。Bot は含めない)
    - transcript: 直近 max_transcript 件の読みやすいやり取り(Bot 発言は「きあら:」表記)
    dict でない要素や user/text 欠落に耐え、例外は投げない。
    """
    bot_mentioned = False
    human_ids: list = []
    lines: list = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        text = m.get("text") or ""
        is_bot = bool(m.get("bot_id")) or (bool(bot_user_id) and m.get("user") == bot_user_id)
        if bot_user_id and bot_user_id in mentioned_user_ids(text):
            bot_mentioned = True
        if not is_bot:
            uid = m.get("user")
            if uid and uid not in human_ids:
                human_ids.append(uid)
        label = "きあら" if is_bot else (m.get("user") or "unknown")
        body = strip_bot_mention(text, bot_user_id) if bot_user_id else text.strip()
        if body:
            lines.append(f"{label}: {body}")
    return bot_mentioned, human_ids, "\n".join(lines[-max_transcript:])


def classify_thread(
    *, bot_mentioned_in_thread: bool, human_participant_ids, speaker_id: str
) -> str:
    """スレッドの状況を "ignore" / "one_on_one" / "group" に分類する。

    - bot_mentioned_in_thread が False → "ignore"(過去にメンションが無いスレッドには入らない)
    - 書き込んだ人間が発言者ただ1人 → "one_on_one"
    - 発言者以外の人間もいる → "group"
    発言者は必ず人間として数える(participants 未収録でも 1対1 と判定できるようにする)。
    """
    if not bot_mentioned_in_thread:
        return "ignore"
    humans = set(human_participant_ids or ())
    humans.add(speaker_id)
    return "one_on_one" if humans == {speaker_id} else "group"


def strip_skip_token(text: str) -> str:
    """グループ応答の skip 印(<skip/>)を本文から除去する。本文が混ざっていても残す。"""
    return _SKIP_RE.sub("", text or "").strip()


def is_silent_reply(text: str) -> bool:
    """グループ応答で「黙る」=投稿しないべき返信か。内部タグと skip 印を落として空なら True。"""
    return strip_skip_token(strip_internal_tags(text)) == ""


_FALSEY = {"false", "0", "no", "off", "none", "null", ""}


def as_bool(value) -> bool:
    """ツール引数の真偽値を頑健に解釈する。

    モデル(特に gpt-oss 系)は bool を文字列 "true"/"false" で渡すことがある。
    Python の bool("false") は True(非空文字列は全部真)になるため、そのまま
    使うと cold=false(塩対応の解除)が効かず「謝っても塩対応が治らない」不具合になる。
    文字列なら "false"/"0"/"no"/"off"/"none"/"null"/空 を偽として扱い、それ以外は bool() に委ねる。
    """
    if isinstance(value, str):
        return value.strip().lower() not in _FALSEY
    return bool(value)


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
