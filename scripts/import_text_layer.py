"""Извлечение текстового слоя из PDF выпуска журнала (PyMuPDF) для сида.

У современных PDF «Компьютерной оптики» есть текстовый слой: его можно взять как
правдоподобный текст страниц, пока не работает OCR (ЛР4).

Пример:
    uv run python scripts/import_text_layer.py data/pdf/2024-01.pdf --year 2024 --number 1 \
        --pages 5-12
Результат: data/text_layer/<year>-<number>.json → {year, number, pages: [{page_number, text_md}]}.
Сид (python -m corag_db.seed) берёт тексты оттуда, если файл для выпуска есть.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import pymupdf

# Перенос слова в конце строки: «дифрак-\nционная» → «дифракционная»
_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
_MANY_BLANK_LINES = re.compile(r"\n{3,}")


def normalize(text: str) -> str:
    """Склеить переносы, убрать пробелы в концах строк и лишние пустые строки."""
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return _MANY_BLANK_LINES.sub("\n\n", text).strip()


def parse_range(spec: str | None) -> tuple[int, int] | None:
    """«5-12» → (5, 12); «7» → (7, 7). Номера страниц PDF с 1."""
    if not spec:
        return None
    start, _, end = spec.partition("-")
    first, last = int(start), int(end or start)
    if not 1 <= first <= last:
        raise ValueError(f"некорректный диапазон страниц: {spec!r}")
    return first, last


def extract_pages(
    pdf_paths: list[Path], page_range: tuple[int, int] | None = None, first_page: int = 1
) -> list[dict]:
    """Тексты страниц нескольких PDF подряд (сквозная нумерация).

    page_range — диапазон по сквозной нумерации PDF; first_page — номер в журнале,
    который получает первая страница PDF (если скан начинается не с 1).
    """
    pages: list[dict] = []
    pdf_page_no = 0
    for path in pdf_paths:
        with pymupdf.open(path) as doc:
            for page in doc:
                pdf_page_no += 1
                if page_range and not page_range[0] <= pdf_page_no <= page_range[1]:
                    continue
                text = normalize(page.get_text("text"))
                if text:
                    pages.append({"page_number": pdf_page_no + first_page - 1, "text_md": text})
    return pages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pdf", nargs="+", type=Path, help="PDF-файлы выпуска (по порядку)")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--number", type=int, required=True)
    parser.add_argument("--pages", help="диапазон страниц PDF, например 5-12")
    parser.add_argument("--first-page", type=int, default=1, help="номер первой страницы PDF")
    parser.add_argument("--out", type=Path, default=Path("data/text_layer"))
    args = parser.parse_args(argv)

    pages = extract_pages(args.pdf, parse_range(args.pages), args.first_page)
    if not pages:
        print("[import] в PDF нет текстового слоя (это скан?) — ничего не записано")
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    out_file = args.out / f"{args.year}-{args.number}.json"
    payload = {"year": args.year, "number": args.number, "pages": pages}
    out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[import] {len(pages)} стр. → {out_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
