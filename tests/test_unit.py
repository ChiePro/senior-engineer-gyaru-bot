"""
単体テスト: bot_core の純粋ロジックと、namespace の整合性。
重い依存(boto3/slack/strands)は不要で実行できる。
"""

from slackbot import core as bot_core
from slackbot import namespaces
from scripts import create_memory


# --- clean_mention ---
def test_clean_mention_strips_mention_and_trims():
    assert bot_core.clean_mention("<@U12345> こんにちは ") == "こんにちは"


def test_clean_mention_handles_multiple_mentions():
    assert bot_core.clean_mention("<@U1> hi <@U2> there") == "hi  there"


def test_clean_mention_handles_none_and_empty():
    assert bot_core.clean_mention(None) == ""
    assert bot_core.clean_mention("   ") == ""


# --- is_slack_retry ---
def test_is_slack_retry_true():
    assert bot_core.is_slack_retry({"headers": {"x-slack-retry-num": "1"}}) is True


def test_is_slack_retry_false_when_absent():
    assert bot_core.is_slack_retry({"headers": {}}) is False
    assert bot_core.is_slack_retry({}) is False


# --- safe_id (AgentCore actorId/sessionId 制約) ---
def test_safe_id_replaces_dot_in_thread_ts():
    # Slack thread_ts はドットを含む → 許可外文字は '-' に
    assert bot_core.safe_id("1719374400.123456") == "1719374400-123456"


def test_safe_id_keeps_valid_user_id():
    assert bot_core.safe_id("U07ABC123") == "U07ABC123"


def test_safe_id_is_deterministic():
    # 同じスレッドは常に同じ id(短期記憶の紐付けが保たれる)
    assert bot_core.safe_id("1719.99") == bot_core.safe_id("1719.99")


def test_safe_id_matches_agentcore_pattern():
    import re as _re
    pat = _re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-_]*$")
    for v in ["1719374400.123456", "_weird", "", "...", "U1"]:
        assert pat.match(bot_core.safe_id(v)), v


# --- strip_internal_tags (内部思考タグの漏れ防止) ---
def test_strip_internal_tags_removes_thinking_block():
    out = bot_core.strip_internal_tags("やっほ!<thinking>内部メモ</thinking>")
    assert out == "やっほ!"
    assert "thinking" not in out


def test_strip_internal_tags_multiline_and_lone():
    txt = "本文\n<thinking>\n複数行\nの思考\n</thinking>\nおわり"
    out = bot_core.strip_internal_tags(txt)
    assert "思考" not in out and "<thinking>" not in out
    assert "本文" in out and "おわり" in out


def test_strip_internal_tags_keeps_normal_text():
    assert bot_core.strip_internal_tags("ふつうの返信だよ") == "ふつうの返信だよ"


# --- strip_bot_mention / mentioned_user_ids (あだ名の対象判別) ---
def test_strip_bot_mention_keeps_others():
    # ボット自身のメンションだけ消え、第三者 <@U_SAKAMOTO> は残る
    out = bot_core.strip_bot_mention("<@BOT> <@U_SAKAMOTO> は坂もっちゃんっていう", "BOT")
    assert out == "<@U_SAKAMOTO> は坂もっちゃんっていう"


def test_strip_bot_mention_handles_labeled_mention():
    out = bot_core.strip_bot_mention("<@BOT|きあら> やっほ", "BOT")
    assert out == "やっほ"


def test_mentioned_user_ids_excludes_bot_and_dedupes():
    txt = "<@BOT> <@U1> と <@U2> と <@U1>"
    assert bot_core.mentioned_user_ids(txt, exclude="BOT") == ["U1", "U2"]


def test_mentioned_user_ids_parses_labeled():
    assert bot_core.mentioned_user_ids("<@U9|name> hi") == ["U9"]


# --- normalize_slack_id (壊れたツール引数の救済) ---
def test_normalize_slack_id_from_mention():
    assert bot_core.normalize_slack_id("<@UQ5JAA3BJ>") == "UQ5JAA3BJ"
    assert bot_core.normalize_slack_id("<@U07ABC123|name>") == "U07ABC123"
    assert bot_core.normalize_slack_id("UAWUA13FW") == "UAWUA13FW"


def test_normalize_slack_id_rejects_garbage():
    assert bot_core.normalize_slack_id("</user_id>") is None
    assert bot_core.normalize_slack_id("") is None
    assert bot_core.normalize_slack_id(None) is None


# --- build_people_note (あだ名 + 特徴) ---
def test_build_people_note_empty():
    assert bot_core.build_people_note({}, "U1") == ""
    # あだ名も特徴も無い人は出さない
    assert bot_core.build_people_note({"U1": {"nickname": None, "notes": []}}, "U1") == ""


def test_build_people_note_marks_speaker_and_includes_notes():
    profiles = {
        "U1": {"nickname": "坂もっちゃん", "notes": ["フロント担当", "猫好き"]},
        "U2": {"nickname": None, "notes": ["朝に弱い"]},
    }
    note = bot_core.build_people_note(profiles, speaker_id="U2")
    assert "U1 = 坂もっちゃん | フロント担当; 猫好き" in note
    assert "U2(発言者本人) | 朝に弱い" in note


# --- build_conversation ---
def _texts(messages):
    return [m["content"][0]["text"] for m in messages]


def _roles(messages):
    return [m["role"] for m in messages]


def assert_valid_converse(messages):
    """Converse の制約: 非空・user 始まり・role 交互。"""
    assert messages, "messages must not be empty"
    assert messages[0]["role"] == "user"
    for i in range(1, len(messages)):
        assert messages[i]["role"] != messages[i - 1]["role"], f"roles not alternating: {_roles(messages)}"


def test_build_conversation_basic_alternation():
    replies = [
        {"user": "U1", "text": "<@BOT> 質問1"},
        {"user": "BOT", "text": "回答1"},
        {"user": "U1", "text": "<@BOT> 質問2"},
    ]
    msgs = bot_core.build_conversation(replies, bot_user_id="BOT")
    assert _roles(msgs) == ["user", "assistant", "user"]
    assert _texts(msgs) == ["質問1", "回答1", "質問2"]
    assert_valid_converse(msgs)


def test_build_conversation_merges_consecutive_same_role():
    replies = [
        {"user": "U1", "text": "一行目"},
        {"user": "U1", "text": "二行目"},
        {"user": "BOT", "text": "返答"},
    ]
    msgs = bot_core.build_conversation(replies, bot_user_id="BOT")
    assert _roles(msgs) == ["user", "assistant"]
    assert _texts(msgs)[0] == "一行目\n二行目"
    assert_valid_converse(msgs)


def test_build_conversation_drops_leading_assistant():
    replies = [
        {"user": "BOT", "text": "先に喋ったボット"},
        {"user": "U1", "text": "ユーザー発言"},
    ]
    msgs = bot_core.build_conversation(replies, bot_user_id="BOT")
    assert _roles(msgs) == ["user"]
    assert_valid_converse(msgs)


def test_build_conversation_skips_subtype_and_empty():
    replies = [
        {"subtype": "channel_join", "user": "U1", "text": "joined"},
        {"user": "U1", "text": "   "},
        {"user": "U1", "text": "<@BOT> 実発言"},
    ]
    msgs = bot_core.build_conversation(replies, bot_user_id="BOT")
    assert _texts(msgs) == ["実発言"]


def test_build_conversation_caps_history():
    replies = []
    for i in range(10):
        replies.append({"user": "U1", "text": f"q{i}"})
        replies.append({"user": "BOT", "text": f"a{i}"})
    msgs = bot_core.build_conversation(replies, bot_user_id="BOT", max_history=4)
    # 直近4件 = q8,a8,q9,a9
    assert _texts(msgs) == ["q8", "a8", "q9", "a9"]
    assert_valid_converse(msgs)


def test_build_conversation_empty_fallback():
    msgs = bot_core.build_conversation([], bot_user_id="BOT")
    assert _roles(msgs) == ["user"]
    assert msgs[0]["content"][0]["text"]  # 非空のフォールバック


# --- build_system_prompt ---
def test_build_system_prompt_without_memory_returns_base():
    base = "ベースプロンプト"
    assert bot_core.build_system_prompt(base, "") == base


def test_build_system_prompt_with_memory_includes_it():
    base = "ベース"
    mem = "・ラーメンが好き\n・犬を飼っている"
    out = bot_core.build_system_prompt(base, mem)
    assert base in out
    assert "ラーメンが好き" in out
    assert "犬を飼っている" in out


# --- namespace 整合 (app_strands.py と create_memory.py が一致する保証) ---
def test_resolve_substitutes_actor_id():
    assert namespaces.resolve(namespaces.NS_PREFERENCES, "U123") == "/users/U123/preferences/"
    assert namespaces.resolve(namespaces.NS_FACTS, "U123") == "/users/U123/facts/"


def test_resolve_substitutes_session_id():
    out = namespaces.resolve(namespaces.NS_SUMMARIES, "U1", session_id="T9")
    assert out == "/users/U1/summaries/T9/"


def test_namespaces_end_with_slash():
    for ns in (namespaces.NS_PREFERENCES, namespaces.NS_FACTS, namespaces.NS_SUMMARIES):
        assert ns.endswith("/"), f"namespace must end with slash: {ns}"


def test_create_memory_strategies_use_same_namespaces():
    """登録側(create_memory)と検索側(namespaces)が単一ソースで一致していること。"""
    by_type = {list(s.keys())[0]: list(s.values())[0] for s in create_memory.STRATEGIES}
    assert by_type["userPreferenceMemoryStrategy"]["namespaces"] == [namespaces.NS_PREFERENCES]
    assert by_type["semanticMemoryStrategy"]["namespaces"] == [namespaces.NS_FACTS]
    assert by_type["summaryMemoryStrategy"]["namespaces"] == [namespaces.NS_SUMMARIES]
