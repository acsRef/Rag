"""migrate/reset 两个运维脚本的纯逻辑单测（DB 交互 mock）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "tools"))

from migrate_vector_dimension import parse_vector_type  # noqa: E402


def test_parse_vector_type_extracts_dimension():
    assert parse_vector_type("vector(4096)") == 4096
    assert parse_vector_type("vector(1024)") == 1024


def test_parse_vector_type_none_for_non_vector():
    assert parse_vector_type("integer") is None
    assert parse_vector_type("text") is None
    assert parse_vector_type("character varying(64)") is None
    assert parse_vector_type(None) is None
