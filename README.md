# Rag — RAG Document Ingestion & Retrieval System

Document ingestion pipeline with parsing, cleaning, smart chunking, and retrieval-augmented generation.

## Architecture

```
Upload → Parser → Cleaner → Structurer → Chunker → Metadata → Embed → ChromaDB
                                                              ↕
                                                           SQLite
```

## Key Features

| Module | Description |
|---|---|
| **Parser** | File-type dispatch via suffix: txt/md/csv (chardet), pdf/docx/pptx/xlsx/html (Docling), images (MiniMax Vision). Embedded image extraction + description. |
| **Cleaner** | 6-step: line endings → control chars → Unicode NFC → invisible chars → page markers → blank lines. Optional punctuation/spacing normalization. |
| **Structurer** | Identify headings, paragraphs, lists, code blocks, tables, images. Marks atomic blocks (table/code/image) as unsplittable. |
| **Chunker** | Structure-aware recursive split (heading > paragraph > line > sentence > char). Atomic block protection, buffer context borrowing, 2-3 sentence overlap. |
| **Metadata** | MiniMax M3 generates title / summary / questions for all chunks in one batch API call. |
| **Indexer** | Full pipeline orchestration → embeddings → ChromaDB + SQLite record. |
| **Vision** | MiniMax M3 image describer with 8-class classification, 5-thread concurrent batch, MD5 cache, small-image filter. |

## Quick Start

```bash
pip install -r requirements.txt
# set .env with API keys
uvicorn app.main:app --reload
```

## Test

```bash
python test/test_parse.py
```
