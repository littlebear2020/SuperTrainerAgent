"""fixed 模式脚本清洗 + 保真修复（Task 5 / R6 白名单标签）。"""

from app.gateway.video_gen.orchestration.script import _strip_visual_markers, split_fixed_script

# ---------- 清洗：剥离画面/镜头/场景标记（ enclosed 整体剥离）----------


def test_strip_visual_markers_brackets():
    """【画面：xxx】被整体剥离，不进 TTS。"""
    assert _strip_visual_markers("【画面：展示产品】这是我们最新的产品。") == "这是我们最新的产品。"


def test_strip_visual_markers_paren_lens():
    """（镜头推近）被整体剥离。"""
    assert _strip_visual_markers("（镜头推近）产品细节清晰可见。") == "产品细节清晰可见。"


def test_strip_visual_markers_english_paren():
    """(lens push in) 英文括号也被剥离。"""
    assert _strip_visual_markers("(close-up) 细节清晰。") == "细节清晰。"


def test_strip_visual_markers_scene_prefix():
    """场景1： / 场景 2：前缀被剥离（内容保留）。"""
    assert _strip_visual_markers("场景1：欢迎来到培训。") == "欢迎来到培训。"
    assert _strip_visual_markers("场景 2：今天讲安全。") == "今天讲安全。"


def test_strip_visual_markers_shot_prefix():
    """镜头： / 镜头1：前缀被剥离。"""
    assert _strip_visual_markers("镜头：特写产品。") == "特写产品。"


def test_strip_visual_markers_narration_label():
    """旁白： / 解说词：标签被剥离（内容保留）。"""
    assert _strip_visual_markers("旁白：这是开场白。") == "这是开场白。"
    assert _strip_visual_markers("解说：第一段内容。") == "第一段内容。"
    assert _strip_visual_markers("解说词：第二段。") == "第二段。"


def test_strip_visual_markers_speaker_label():
    """方括号标签 [旁白] 被剥离；角色名+冒号（张经理：）不剥（R6：那是台词不是画面标记）。"""
    assert _strip_visual_markers("[旁白] 开场介绍。") == "开场介绍。"
    assert _strip_visual_markers("[Sound Effect] 爆炸声") == "爆炸声"
    assert _strip_visual_markers("张经理：大家好。") == "张经理：大家好。"


def test_strip_visual_markers_does_not_touch_legitimate_colon_lines():
    """R6：合法的'短文本+冒号'内容行不被误伤。"""
    assert _strip_visual_markers("注意：安全第一。") == "注意：安全第一。"
    assert _strip_visual_markers("时间：下午三点。") == "时间：下午三点。"


def test_strip_visual_markers_preserves_legitimate_paren_content():
    """合法中文括注（型号/时长/附录）不误剥——仅剥含镜头/画面/特写等关键词的括号。"""
    assert _strip_visual_markers("产品（旗舰版）介绍。") == "产品（旗舰版）介绍。"
    assert _strip_visual_markers("时长（约30秒）。") == "时长（约30秒）。"
    assert _strip_visual_markers("详见附录（A）。") == "详见附录（A）。"


def test_strip_visual_markers_strips_keyword_parens_anywhere():
    """括号内容含镜头/画面/特写/场景关键词（不论位置）才剥离。"""
    assert _strip_visual_markers("（镜头推近）产品。") == "产品。"
    assert _strip_visual_markers("（特写产品）展示。") == "展示。"
    assert _strip_visual_markers("（场景描述：教室）讲解。") == "讲解。"


def test_strip_visual_markers_bold_markdown():
    """**画面**： 加粗标签语法被剥离（仅标签前缀；同行的画面描述属 Task 10 解析器范畴）。"""
    assert _strip_visual_markers("**画面**：展示图表。内容如下。") == "展示图表。内容如下。"


# ---------- 保真：末句无标点不丢 ----------


def test_last_sentence_without_punctuation_not_dropped():
    """末句无尾标点不被静默丢弃。"""
    text = "第一句。第二句。第三句"
    parts = split_fixed_script(text)
    assert parts == ["第一句。", "第二句。", "第三句"]


# ---------- 保真：换行是切分边界 ----------


def test_newline_is_split_boundary():
    """单换行也是切分边界（与前端提示一致）。"""
    text = "第一句\n第二句。"
    parts = split_fixed_script(text)
    assert "第一句" in parts
    assert "第二句。" in parts


def test_newline_without_punctuation():
    """换行分隔的句子即使无标点也各自成段。"""
    parts = split_fixed_script("开场白\n第二段\n第三段")
    assert parts == ["开场白", "第二段", "第三段"]


# ---------- 综合清洗 ----------


def test_full_dirty_script_cleaned_and_split():
    """含多种标记的脏脚本被清洗 + 正确切分。"""
    text = "场景1：【画面：教室全景】\n旁白：欢迎来到安全培训。\n（镜头推近讲师）\n今天我们讲消防安全。"
    parts = split_fixed_script(text)
    joined = "".join(parts)
    assert "画面" not in joined
    assert "镜头" not in joined
    assert "场景" not in joined
    assert "旁白" not in joined
    assert "欢迎来到安全培训。" in parts
    assert "今天我们讲消防安全。" in parts


def test_paragraph_mode_preserved():
    """paragraph 模式仍按空行切分且经过清洗。"""
    parts = split_fixed_script("【画面：A】段落一\n\n【画面：B】段落二", split_mode="paragraph")
    assert parts == ["段落一", "段落二"]
