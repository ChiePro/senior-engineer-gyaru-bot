"""
ペルソナ(ギャル口調)の単体テスト。
重い依存(boto3/slack/strands)なしで、人格定義そのものを検証する。
"""

from slackbot import persona
from slackbot import core as bot_core


def test_persona_is_nonempty_and_japanese_instruction():
    assert persona.PERSONA.strip()
    assert "日本語" in persona.PERSONA


def test_persona_has_gyaru_tone_markers():
    """ほどよくギャル: 口調マーカーが含まれること。"""
    markers = ["ギャル", "っしょ", "マジ", "まじ", "タメ口"]
    assert any(m in persona.PERSONA for m in markers), persona.PERSONA


def test_persona_keeps_technical_accuracy_guardrail():
    """口調はギャルでも、コード/コマンドは正確に保つ指示が残っていること。"""
    assert "コマンド" in persona.PERSONA
    assert "正確" in persona.PERSONA


def test_strands_prompt_extends_persona_with_memory_hint():
    """Strands 版は核 + 長期記憶の活用を促す一文。"""
    assert persona.PERSONA in persona.STRANDS_SYSTEM_PROMPT
    assert "長期的な情報" in persona.STRANDS_SYSTEM_PROMPT
    assert len(persona.STRANDS_SYSTEM_PROMPT) > len(persona.PERSONA)


def test_strands_prompt_instructs_to_use_injected_clock():
    """日付・時刻はシステムから渡される現在の日時(JST)を使う指示が含まれること。"""
    p = persona.STRANDS_SYSTEM_PROMPT
    assert "日時" in p and "JST" in p
    assert "日付" in p or "時刻" in p


def test_fallback_message_is_nonempty_and_gyaru():
    """失敗時の返信も素の謝罪ではなくギャル口調を保つ。"""
    assert persona.FALLBACK_MESSAGE.strip()
    assert "すみません" not in persona.FALLBACK_MESSAGE


def test_behavior_guide_mentions_all_tools():
    """あだ名・特徴・機嫌のツール名がガイドに含まれていること。"""
    assert "set_nickname" in persona.BEHAVIOR_GUIDE
    assert "remember_about" in persona.BEHAVIOR_GUIDE
    assert "set_mood" in persona.BEHAVIOR_GUIDE


def test_behavior_guide_says_saving_is_silent():
    """保存をいちいち宣言しない方針が含まれていること。"""
    assert "宣言しない" in persona.BEHAVIOR_GUIDE


def test_behavior_guide_mentions_web_search_policy():
    """Web検索ツールの使い方(最新性が要るときだけ/要約して返す)が含まれていること。"""
    assert "web_search" in persona.BEHAVIOR_GUIDE
    assert "最新" in persona.BEHAVIOR_GUIDE


def test_cold_mode_note_is_about_apology_recovery():
    """塩対応注記は『謝られたら解除して戻す』方針を含む。"""
    assert "謝" in persona.COLD_MODE_NOTE
    assert "set_mood" in persona.COLD_MODE_NOTE


def test_group_reply_guide_enables_active_participation_and_skip():
    """自発参加ガイド: 積極参加 + 黙るときの skip トークン + 技術正確さ。"""
    guide = persona.GROUP_REPLY_GUIDE
    assert guide.strip()
    # 関係あれば積極的に参加する方針
    assert "積極" in guide
    # 黙るときは skip トークンだけ返す(core と同じトークンを使う=drift 防止)
    assert bot_core.SKIP_TOKEN in guide
    # 技術的な中身は崩さない
    assert "正確" in guide


def test_speak_gate_prompt_is_conservative_and_one_word():
    """発話ゲートは原則 NO・YES/NO の1語出力・他人宛て/相槌/沈黙コメントを NO にする。"""
    g = persona.SPEAK_GATE_PROMPT
    assert g.strip()
    assert "YES" in g and "NO" in g
    assert "原則" in g  # 原則 NO に倒す
    # 他人宛て(あだ名/名前呼びかけ)と、きあらの沈黙へのコメントは NO 側に明記
    assert "あだ名" in g or "名前" in g
    assert "沈黙" in g


def test_behavior_guide_does_not_narrate_silence():
    """自分の沈黙・skip・内部のふるまいを実況しない方針が含まれていること。"""
    assert "実況" in persona.BEHAVIOR_GUIDE or "黙ったこと" in persona.BEHAVIOR_GUIDE


def test_group_reply_guide_skips_when_addressed_to_others():
    """他人宛て(@だけでなく あだ名・名前の呼びかけ)には割り込まない方針が明記されていること。"""
    guide = persona.GROUP_REPLY_GUIDE
    assert "あだ名" in guide or "名前" in guide
    # 「自分宛てか」を判断軸にする
    assert "自分宛て" in guide


def test_abe_mode_note_keeps_accuracy_and_valid_probability():
    """安倍モード注記は口調(地の文)だけ上書きし、技術的中身は正確に保つ。確率は 0〜1。"""
    assert persona.ABE_MODE_NOTE.strip()
    assert "正確" in persona.ABE_MODE_NOTE
    assert 0 < persona.ABE_MODE_PROBABILITY <= 1
