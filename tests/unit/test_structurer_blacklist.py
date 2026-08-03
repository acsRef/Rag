"""标题黑名单：精确匹配，不再子串误杀真实章节。"""
from app.ingestion.structurer import document_structurer

NL = chr(10)


def test_boilerplate_exact_title_dropped():
    md = NL.join(["# 文档", "## 目录", "这里只是页脚样板。", "## 正文", "真正的内容。"])
    sections = document_structurer.structure(md)
    titles = [s.title for s in sections]
    assert "正文" in titles
    assert "目录" not in titles
    assert all("这里只是页脚样板" not in (e.text or "") for s in sections for e in s.elements)


def test_legitimate_section_not_dropped():
    md = NL.join(["# 手册", "## 目录结构说明", "本节描述目录如何组织。", "## 其他", "内容。"])
    sections = document_structurer.structure(md)
    texts = " ".join(e.text or "" for s in sections for e in s.elements)
    assert "本节描述目录如何组织" in texts, "真实章节被黑名单子串误杀"
