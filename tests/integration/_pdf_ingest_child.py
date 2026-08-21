"""子进程执行 PDF 摄入。

docling 解析复杂页面可能触发原生 std::bad_alloc（Python 不可捕获、直接杀进程）。
在子进程里跑摄入：原生崩溃只会终止子进程，pytest 主进程不受牵连。
用法：python _pdf_ingest_child.py <pdf 路径>
输出：RESULT_JSON:<摄入结果 JSON>
"""

import json
import sys
from pathlib import Path

from app.ingestion.indexer import document_indexer

pdf = Path(sys.argv[1])
res = document_indexer.index(pdf.name, pdf.read_bytes(), kb_id="test-kb", user_id="test-user")
print("RESULT_JSON:" + json.dumps(res))
