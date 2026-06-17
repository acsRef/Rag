import os
import sys

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _try in [_project_root, os.getcwd(), os.path.join(os.getcwd(), "..")]:
    _try = os.path.abspath(_try)
    if os.path.exists(os.path.join(_try, "app")):
        _project_root = _try
        break
sys.path.insert(0, _project_root)

from app.ingestion.parser import document_parser
from app.ingestion.cleaner import document_cleaner
from app.ingestion.structurer import document_structurer
from app.ingestion.chunker import text_chunker


def test_parse(filepath: str, filename: str | None = None):
    if filename is None:
        filename = filepath.split("/")[-1].split("\\")[-1]

    with open(filepath, "rb") as f:
        content = f.read()

    print(f"{'='*60}")
    print(f"文件: {filename} ({len(content)} bytes)")
    print(f"{'='*60}")

    # 1. 解析
    print("\n[1/4] 解析...")
    text = document_parser.parse_bytes(content, filename)
    print(f"    输出长度: {len(text)} 字符")
    print(f"    前200字:  {text[:200]}")

    # 2. 清洗
    print("\n[2/4] 清洗...")
    text = document_cleaner.clean(text)
    print(f"    清洗后长度: {len(text)} 字符")

    # 3. 结构分析
    print("\n[3/4] 结构分析...")
    sections = document_structurer.structure(text)
    print(f"    Section 数: {len(sections)}")
    for s in sections:
        tag = f"{'#' * s.level} {s.title}" if s.title else "(无标题)"
        print(f"    ├─ {tag}")
        for e in s.elements:
            atomic = " [原子]" if e.is_atomic else ""
            preview = e.text[:60].replace("\n", " ")
            print(f"    │   └─ {e.type}{atomic}: {preview}...")

    # 4. 切分
    print("\n[4/4] 智能切分...")
    chunks = text_chunker.chunk(sections)
    print(f"    切分块数: {len(chunks)}")
    for i, c in enumerate(chunks):
        print(f"\n    ─── Chunk {i} ({len(c.text)} chars) ───")
        if c.title:
            print(f"    title: {c.title}")
        print(f"    text: {c.text[:200]}...")
        print(f"    ───────────────────────")

    print(f"\n{'='*60}")
    print(f"完成: {len(chunks)} 个 Chunk")
    print(f"{'='*60}")


if __name__ == "__main__":
    filepath = os.path.join(os.path.dirname(__file__), "test_sample.txt")
    test_parse(filepath)
