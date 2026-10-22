"""Импорт текстового слоя PDF: сгенерированный PDF → JSON для сида."""

import importlib.util
import json
from pathlib import Path

import pymupdf
import pytest

_SCRIPT = Path(__file__).parents[2] / "scripts" / "import_text_layer.py"
_spec = importlib.util.spec_from_file_location("import_text_layer", _SCRIPT)
itl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(itl)


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    """3 страницы с кириллицей (встроенный шрифт PyMuPDF с кириллицей), третья с переносом."""
    path = tmp_path / "issue.pdf"
    with pymupdf.open() as doc:
        for text in ("Первая страница", "Вторая страница", "Дифрак-\nционная оптика"):
            doc.new_page().insert_text((72, 72), text, fontname="china-s")
        doc.save(path)
    return path


def test_extract_pages(pdf):
    pages = itl.extract_pages([pdf])
    assert [p["page_number"] for p in pages] == [1, 2, 3]
    assert pages[0]["text_md"] == "Первая страница"
    assert pages[2]["text_md"] == "Дифракционная оптика"


def test_range_and_first_page(pdf):
    pages = itl.extract_pages([pdf], itl.parse_range("2-3"), first_page=101)
    assert [p["page_number"] for p in pages] == [102, 103]


def test_main_writes_json(pdf, tmp_path):
    out = tmp_path / "out"
    assert itl.main([str(pdf), "--year", "2024", "--number", "1", "--out", str(out)]) == 0
    payload = json.loads((out / "2024-1.json").read_text(encoding="utf-8"))
    assert (payload["year"], payload["number"], len(payload["pages"])) == (2024, 1, 3)


@pytest.mark.parametrize("spec", ["0-3", "5-2", "abc"])
def test_bad_range(spec):
    with pytest.raises(ValueError):
        itl.parse_range(spec)
