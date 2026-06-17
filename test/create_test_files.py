import os

base = os.path.dirname(os.path.abspath(__file__))

dirty = "\ufeff" + "--- Page 1 ---\n"
dirty += "Hello\r\nWorld\x00Test\r\n"
dirty += "\n" * 5
dirty += "--- Page 2 ---\n"
dirty += "Normal line\n"
dirty += "Line with\xa0nbsp\n"
dirty += "\u200bZero-width\u200cchars\n"
dirty += "\n" * 4
dirty += "Final line"

with open(os.path.join(base, "test_dirty.txt"), "w", encoding="utf-8") as f:
    f.write(dirty)
print("Created: test_dirty.txt")

gbk_text = "你好世界\n这是一个GBK编码的测试文件\n用于测试编码检测功能"
with open(os.path.join(base, "test_gbk.txt"), "w", encoding="gbk") as f:
    f.write(gbk_text)
print("Created: test_gbk.txt")

fancy = 'He said \u201cHello World\u201d and left.\n'
fancy += 'It was a \u2018great\u2019 day \u2014 indeed.\n'
fancy += 'She replied: \u201cI\u2019ll be there\u201d\n'
with open(os.path.join(base, "test_fancy.txt"), "w", encoding="utf-8") as f:
    f.write(fancy)
print("Created: test_fancy.txt")

chinese = "人工智能（AI）是计算机科学的一个分支。\n\n"
chinese += "它致力于创建能够模拟人类智能的系统。\n"
chinese += "主要包括以下几个领域：\n"
chinese += "1. 机器学习\n2. 深度学习\n3. 自然语言处理\n4. 计算机视觉\n\n"
chinese += "深度学习是机器学习的一个子集。\n"
chinese += "它使用多层神经网络来学习数据的表示。"
with open(os.path.join(base, "test_chinese.txt"), "w", encoding="utf-8") as f:
    f.write(chinese)
print("Created: test_chinese.txt")

print("\nAll test files created!")
