"""
ペルソナ(ギャル口調)の単体テスト。
重い依存(boto3/slack/strands)なしで、人格定義そのものを検証する。
"""

from slackbot import persona


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


def test_cold_mode_note_is_about_apology_recovery():
    """塩対応注記は『謝られたら解除して戻す』方針を含む。"""
    assert "謝" in persona.COLD_MODE_NOTE
    assert "set_mood" in persona.COLD_MODE_NOTE
