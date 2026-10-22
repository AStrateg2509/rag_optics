"""Тестовый PDF для демонстрации загрузки скана (ЛР2).

    uv run python scripts/make_test_pdf.py data/demo/scan.pdf --pages 8
    uv run python scripts/make_test_pdf.py data/demo/big.pdf --pages 2 --pad-mb 195

--pad-mb добавляет вложенный файл со случайными байтами: PDF остаётся корректным,
а размер растёт до нужного (проверка client_max_body_size у Nginx и лимита MAX_SCAN_MB).
"""

import argparse
import os
from pathlib import Path

import pymupdf


def make_pdf(pages: int = 3, pad_bytes: int = 0) -> bytes:
    """PDF из pages страниц с подписью «Страница N»; pad_bytes — размер вложения-балласта."""
    with pymupdf.open() as doc:
        for n in range(1, pages + 1):
            # china-s — встроенный в PyMuPDF шрифт с кириллицей
            doc.new_page().insert_text((72, 72), f"Страница {n}", fontname="china-s")
        if pad_bytes:
            # случайные байты не сжимаются, поэтому размер файла ≈ pad_bytes
            doc.embfile_add("padding.bin", os.urandom(pad_bytes))
        return doc.tobytes()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path)
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--pad-mb", type=float, default=0, help="балласт, МБ")
    args = parser.parse_args()

    data = make_pdf(args.pages, int(args.pad_mb * 1024 * 1024))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print(f"{args.output}: {args.pages} стр., {len(data) / 2**20:.1f} МБ")


if __name__ == "__main__":
    main()
