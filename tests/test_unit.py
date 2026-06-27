"""
単体テスト: core の純粋ロジックと、namespace の整合性。
重い依存(boto3/slack/strands)は不要で実行できる。
"""

from slackbot import core as bot_core
from slackbot import namespaces
from scripts import create_memory


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


# --- as_bool (ツール引数の真偽値を頑健に解釈 / 塩対応の解除が効かない不具合対策) ---
def test_as_bool_string_false_is_false():
    # モデルが文字列 "false" を渡しても解除(False)になること(bool("false") は True の罠)
    for v in ["false", "False", "FALSE", " false ", "0", "no", "off", "none", "null", ""]:
        assert bot_core.as_bool(v) is False, v


def test_as_bool_string_true_is_true():
    for v in ["true", "True", "1", "yes", "on", "cold"]:
        assert bot_core.as_bool(v) is True, v


def test_as_bool_passes_through_real_bool():
    assert bot_core.as_bool(True) is True
    assert bot_core.as_bool(False) is False


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


# --- build_nickname_directory (全員のあだ名逆引き辞書) ---
def test_build_nickname_directory_empty():
    assert bot_core.build_nickname_directory({}) == ""
    # あだ名が空(None)の人だけなら出さない
    assert bot_core.build_nickname_directory({"U1": None}) == ""


def test_build_nickname_directory_lists_all_known_nicknames():
    # 会話に未登場でも、あだ名で呼ばれたとき誰か照合できるよう全件入れる
    nicknames = {"U1": "坂もっちゃん", "U2": "ゆうちゃん"}
    note = bot_core.build_nickname_directory(nicknames)
    assert "U1 = 坂もっちゃん" in note
    assert "U2 = ゆうちゃん" in note
    # あだ名が無い人は混ぜない
    assert "U3" not in bot_core.build_nickname_directory({"U1": "坂もっちゃん", "U3": None})


# --- format_search_results (Web検索結果の整形) ---
def test_format_search_results_empty():
    assert bot_core.format_search_results([]) == ""
    assert bot_core.format_search_results(None) == ""


def test_format_search_results_includes_title_and_url():
    results = [
        {"title": "Python 3.13 リリース", "url": "https://example.com/py313", "content": "新機能の概要。"},
        {"title": "Strands Agents とは", "url": "https://example.com/strands", "content": "概要説明。"},
    ]
    out = bot_core.format_search_results(results)
    assert "Python 3.13 リリース" in out
    assert "https://example.com/py313" in out
    assert "Strands Agents とは" in out


def test_format_search_results_caps_item_count():
    results = [
        {"title": f"記事{i}", "url": f"https://example.com/{i}", "content": "本文"}
        for i in range(10)
    ]
    out = bot_core.format_search_results(results, max_items=3)
    # 上位3件だけ(1件=行頭 "- " の1行)
    item_lines = [ln for ln in out.splitlines() if ln.startswith("- ")]
    assert len(item_lines) == 3


def test_format_search_results_truncates_by_chars_without_breaking_url():
    long_content = "あ" * 500
    results = [
        {"title": f"記事{i}", "url": f"https://example.com/article/{i}", "content": long_content}
        for i in range(5)
    ]
    out = bot_core.format_search_results(results, max_items=5, max_chars=300)
    assert len(out) <= 300
    # 先頭の件は残り、URL が途中で切れていない(載っている URL は完全形)
    assert "https://example.com/article/0" in out


def test_format_search_results_tolerates_missing_fields():
    # title / content 欠落でも例外を投げない
    out = bot_core.format_search_results([{"url": "https://example.com/x"}])
    assert "https://example.com/x" in out
    assert bot_core.format_search_results([{}]) is not None


# --- namespace 整合 (socket_app の retrieval と create_memory の登録が一致する保証) ---
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
