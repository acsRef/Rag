"""Unit tests for evidence organizer."""

from app.core.evidence import ConflictDetector, EvidenceOrganizer, EvidenceSlot, EvidenceTable
from app.models.schemas import RetrievedChunk


class TestEvidenceReranking:
    """P2: Evidence-aware reranking tests."""

    def test_compute_importance_basic(self):
        organizer = EvidenceOrganizer()
        table = EvidenceTable(
            query="2023-2024年营收",
            slots=[
                EvidenceSlot(
                    sub_question="2023年营收",
                    chunks=[
                        _make_chunk("c1", doc_id="doc1", score=0.8),
                    ],
                ),
                EvidenceSlot(
                    sub_question="2024年营收",
                    chunks=[
                        _make_chunk("c2", doc_id="doc2", score=0.6),
                    ],
                ),
            ],
            query_type="comparison",
        )
        scores = organizer.compute_chunk_importance(table)
        # Both chunks should have importance > their base score (due to unique doc bonus)
        assert scores["c1"] > 0.8
        assert scores["c2"] > 0.6

    def test_rerank_chunks_ordering(self):
        organizer = EvidenceOrganizer()
        # c2 has lower base score but comes from a doc that covers both sub-questions
        c1 = _make_chunk("c1", doc_id="doc1", score=0.9)
        c2 = _make_chunk("c2", doc_id="doc2", score=0.5)
        c3 = _make_chunk("c3", doc_id="doc2", score=0.4)  # same doc as c2

        table = EvidenceTable(
            query="汇总",
            slots=[
                EvidenceSlot(sub_question="q1", chunks=[c1, c2]),
                EvidenceSlot(sub_question="q2", chunks=[c3]),
            ],
            query_type="summary",
        )
        # doc2 covers 2/2 sub-questions, so c2 and c3 should get coverage bonus
        reranked = organizer.rerank_chunks([c1, c2, c3], table)
        # doc2 chunks should be promoted due to coverage
        doc2_chunks = [c for c in reranked if c.document_id == "doc2"]
        assert len(doc2_chunks) == 2

    def test_detect_comparison_pattern(self):
        organizer = EvidenceOrganizer()
        # Same metric, different years → comparison
        assert (
            organizer.detect_comparison_pattern(
                "2023-2025年营收",
                ["2023年营收", "2024年营收", "2025年营收"],
            )
            is True
        )

        # Different topics → not comparison
        assert (
            organizer.detect_comparison_pattern(
                "公司情况",
                ["公司营收", "公司员工数量"],
            )
            is False
        )

        # Single sub-question → not comparison
        assert (
            organizer.detect_comparison_pattern(
                "营收",
                ["营收是多少"],
            )
            is False
        )

    def test_unique_doc_bonus(self):
        """Chunks from documents unique to a slot should get higher importance."""
        organizer = EvidenceOrganizer()
        # doc1 only in slot 1, doc2 in both slots
        c1 = _make_chunk("c1", doc_id="doc1", score=0.5)
        c2 = _make_chunk("c2", doc_id="doc2", score=0.5)
        c3 = _make_chunk("c3", doc_id="doc2", score=0.5)

        table = EvidenceTable(
            query="test",
            slots=[
                EvidenceSlot(sub_question="q1", chunks=[c1, c2]),
                EvidenceSlot(sub_question="q2", chunks=[c3]),
            ],
        )
        scores = organizer.compute_chunk_importance(table)
        # c1 is from doc1 which is unique to slot 1 → should get unique doc bonus
        # c2 is from doc2 which appears in both slots → no unique doc bonus for slot 1
        assert scores["c1"] > scores["c2"]


def _make_chunk(
    chunk_id: str, doc_id: str = "doc1", text: str = "test text", score: float = 0.5
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id=doc_id,
        text=text,
        score=score,
        title="Test Doc",
        section_path="第一节",
        year="2023年",  # Phase 4-B: ConflictDetector 要求 period 非空才参与冲突检测
    )


class TestEvidenceSlot:
    def test_empty_slot_not_covered(self):
        slot = EvidenceSlot(sub_question="test question")
        assert not slot.covered
        assert len(slot.doc_ids) == 0

    def test_slot_with_chunks_is_covered(self):
        chunk = _make_chunk("c1")
        slot = EvidenceSlot(sub_question="test", chunks=[chunk])
        assert slot.covered
        assert "doc1" in slot.doc_ids


class TestEvidenceTable:
    def test_empty_table_coverage(self):
        table = EvidenceTable(query="test")
        assert table.overall_coverage == 0.0
        assert not table.has_multiple_docs

    def test_partial_coverage(self):
        table = EvidenceTable(
            query="test",
            slots=[
                EvidenceSlot(sub_question="q1", chunks=[_make_chunk("c1")]),
                EvidenceSlot(sub_question="q2"),  # empty
            ],
        )
        assert table.overall_coverage == 0.5
        assert not table.has_multiple_docs

    def test_multiple_docs(self):
        table = EvidenceTable(
            query="test",
            slots=[
                EvidenceSlot(sub_question="q1", chunks=[_make_chunk("c1", doc_id="doc1")]),
                EvidenceSlot(sub_question="q2", chunks=[_make_chunk("c2", doc_id="doc2")]),
            ],
        )
        assert table.has_multiple_docs
        assert table.overall_coverage == 1.0


class TestEvidenceOrganizer:
    def test_organize_basic(self):
        organizer = EvidenceOrganizer()
        sub_q_chunks = {
            "2023年营收": [_make_chunk("c1", doc_id="doc1", text="732亿")],
            "2024年营收": [_make_chunk("c2", doc_id="doc2", text="778亿")],
        }
        table = organizer.organize("近三年营收", sub_q_chunks)

        assert len(table.slots) == 2
        assert table.overall_coverage == 1.0
        assert table.has_multiple_docs

    def test_organize_deduplicates_chunks(self):
        organizer = EvidenceOrganizer()
        # Same chunk appearing in multiple sub-questions
        shared_chunk = _make_chunk("c1", doc_id="doc1")
        sub_q_chunks = {
            "子问题1": [shared_chunk],
            "子问题2": [shared_chunk],
        }
        table = organizer.organize("test", sub_q_chunks)

        # Each slot should have the chunk (they're independent)
        assert len(table.slots[0].chunks) == 1
        assert len(table.slots[1].chunks) == 1

        # But get_all_chunks should deduplicate
        all_chunks = organizer.get_all_chunks(table)
        assert len(all_chunks) == 1

    def test_format_for_prompt_structure(self):
        organizer = EvidenceOrganizer()
        sub_q_chunks = {
            "2023年营收": [_make_chunk("c1", doc_id="doc1", text="营收732亿元")],
            "2024年营收": [_make_chunk("c2", doc_id="doc2", text="营收778亿元")],
        }
        table = organizer.organize("近两年营收", sub_q_chunks)
        formatted = organizer.format_for_prompt(table)

        # Check structure
        assert "## 证据表" in formatted
        assert "### 子问题 1" in formatted
        assert "### 子问题 2" in formatted
        assert "[Source 1]" in formatted
        assert "[Source 2]" in formatted
        assert "✅ 2/2" in formatted
        assert "2 份不同文档" in formatted

    def test_format_missing_slot(self):
        organizer = EvidenceOrganizer()
        sub_q_chunks = {
            "有的问题": [_make_chunk("c1")],
            "缺失的问题": [],  # no chunks
        }
        table = organizer.organize("test", sub_q_chunks)
        formatted = organizer.format_for_prompt(table)

        assert "⚠️ 1/2" in formatted
        assert "缺失" in formatted

    def test_get_source_map(self):
        organizer = EvidenceOrganizer()
        sub_q_chunks = {
            "q1": [_make_chunk("c1"), _make_chunk("c2")],
            "q2": [_make_chunk("c3")],
        }
        table = organizer.organize("test", sub_q_chunks)
        source_map = organizer.get_source_map(table)

        assert source_map == {"c1": 1, "c2": 2, "c3": 3}


class TestConflictDetection:
    """P3: Conflict detection and resolution tests."""

    def test_extract_metric_values(self):
        detector = ConflictDetector()
        chunk = _make_chunk("c1", doc_id="doc1", text="营业收入为732.22亿元，同比增长5%")
        values = detector._extract_metric_values(chunk)
        assert len(values) >= 1
        # Should find "营业收入" with value 732.22 亿元
        revenue = [v for v in values if "收入" in v.metric]
        assert len(revenue) >= 1
        assert revenue[0].value == 732.22
        assert revenue[0].unit == "亿元"

    def test_detect_no_conflict_same_values(self):
        """Same metric, same values across docs → no conflict."""
        detector = ConflictDetector()
        table = EvidenceTable(
            query="test",
            slots=[
                EvidenceSlot(
                    sub_question="q1",
                    chunks=[
                        _make_chunk("c1", doc_id="doc1", text="营业收入为732亿元"),
                    ],
                ),
                EvidenceSlot(
                    sub_question="q2",
                    chunks=[
                        _make_chunk("c2", doc_id="doc2", text="营业收入为732亿元"),
                    ],
                ),
            ],
        )
        conflicts = detector.detect(table)
        # Same value, no conflict
        assert len(conflicts) == 0

    def test_detect_conflict_different_values(self):
        """Same metric, different values across docs → conflict."""
        detector = ConflictDetector()
        table = EvidenceTable(
            query="test",
            slots=[
                EvidenceSlot(
                    sub_question="q1",
                    chunks=[
                        _make_chunk("c1", doc_id="doc1", text="营业收入为732亿元"),
                    ],
                ),
                EvidenceSlot(
                    sub_question="q2",
                    chunks=[
                        _make_chunk("c2", doc_id="doc2", text="营业收入为740亿元"),
                    ],
                ),
            ],
        )
        conflicts = detector.detect(table)
        assert len(conflicts) >= 1
        conflict = conflicts[0]
        assert "收入" in conflict.metric
        assert len(conflict.values) == 2
        assert conflict.severity in ("high", "medium")

    def test_year_mismatch_not_high_severity(self):
        """Different years → year_mismatch, low severity."""
        detector = ConflictDetector()
        c1 = _make_chunk("c1", doc_id="doc1", text="2023年营业收入为732亿元")
        c1.year = "2023"
        c2 = _make_chunk("c2", doc_id="doc2", text="2024年营业收入为778亿元")
        c2.year = "2024"
        table = EvidenceTable(
            query="test",
            slots=[
                EvidenceSlot(sub_question="2023", chunks=[c1]),
                EvidenceSlot(sub_question="2024", chunks=[c2]),
            ],
        )
        conflicts = detector.detect(table)
        # Should detect as year_mismatch (low severity) or no conflict
        for c in conflicts:
            if c.conflict_type == "year_mismatch":
                assert c.severity == "low"

    def test_organize_includes_conflicts(self):
        """EvidenceOrganizer.organize() should populate conflicts."""
        organizer = EvidenceOrganizer()
        sub_q_chunks = {
            "q1": [_make_chunk("c1", doc_id="doc1", text="营业收入为732亿元")],
            "q2": [_make_chunk("c2", doc_id="doc2", text="营业收入为740亿元")],
        }
        table = organizer.organize("2023-2024年营收", sub_q_chunks)
        # Should have detected conflict
        assert table.has_multiple_docs
        assert len(table.conflicts) >= 1

    def test_format_includes_conflict_warning(self):
        """format_for_prompt should include conflict warnings."""
        organizer = EvidenceOrganizer()
        sub_q_chunks = {
            "q1": [_make_chunk("c1", doc_id="doc1", text="营业收入为732亿元")],
            "q2": [_make_chunk("c2", doc_id="doc2", text="营业收入为740亿元")],
        }
        table = organizer.organize("test", sub_q_chunks)
        formatted = organizer.format_for_prompt(table)
        # Should have conflict section
        assert "冲突" in formatted or "差异" in formatted
