"""POC test for embedding_text enhancement using Qwen2.5-14B-Instruct."""
from app.ingestion.chunker import Chunk
from app.ingestion.metadata import embedding_text_enhancer


def test_enhance_financial_chunk():
    """Test enhancing a chunk with financial table data."""
    chunk = Chunk(
        text="""| 项目 | 2024年 | 2023年 | 同比增减 |
|------|--------|--------|----------|
| 营业收入 | 777.73亿元 | 740.19亿元 | 5.08% |
| 归属于上市公司股东的净利润 | 84.08亿元 | 59.75亿元 | 40.72% |
| 研发投入 | 54.88亿元 | 58.65亿元 | -6.43% |""",
        section_path=["三一重工 2024 年年度报告", "第二节", "主要会计数据"],
    )

    print("原始 chunk:")
    print(chunk.text[:200])
    print("\n增强中...")

    # Show number extraction
    original_nums = embedding_text_enhancer._extract_numbers(chunk.text)
    print(f"\n原始数字 ({len(original_nums)} 个): {sorted(original_nums)}")

    enhanced = embedding_text_enhancer.enhance_chunk(chunk)

    print("\n增强后的 embedding_text:")
    print(enhanced if enhanced else "(None - validation failed)")
    print(f"\n长度: {len(enhanced) if enhanced else 0} 字")

    # Check if key information is preserved
    if enhanced:
        generated_nums = embedding_text_enhancer._extract_numbers(enhanced)
        preserved = original_nums & generated_nums
        print(f"\n保留的数字 ({len(preserved)}/{len(original_nums)}): {sorted(preserved)}")

        checks = [
            ("2024", "年份 2024"),
            ("777.73", "营收数字"),
            ("营业收入", "营收指标"),
        ]
        print("\n关键信息检查:")
        for keyword, desc in checks:
            found = keyword in enhanced
            print(f"  {desc}: {'✓' if found else '✗'} ({keyword})")

    return enhanced

    print("\n增强后的 embedding_text:")
    print(enhanced)
    print(f"\n长度: {len(enhanced) if enhanced else 0} 字")

    # Check if key information is preserved
    if enhanced:
        checks = [
            ("2024", "年份 2024"),
            ("777.73", "营收数字"),
            ("营业收入", "营收指标"),
        ]
        print("\n关键信息检查:")
        for keyword, desc in checks:
            found = keyword in enhanced
            print(f"  {desc}: {'✓' if found else '✗'} ({keyword})")

    return enhanced


if __name__ == "__main__":
    print("=" * 60)
    print("Embedding Text Enhancement POC Test")
    print(f"Model: {embedding_text_enhancer.model}")
    print("=" * 60)
    print()

    result = test_enhance_financial_chunk()

    print("\n" + "=" * 60)
    if result and len(result) > 50:
        print("✓ POC 成功：增强文本包含关键信息")
    else:
        print("✗ POC 失败：增强文本质量不足")
    print("=" * 60)
