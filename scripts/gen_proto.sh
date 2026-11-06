#!/usr/bin/env bash
# Генерация Python-кода из proto/rag/v1/rag.proto (grpcio-tools из dev-группы uv).
#
#   bash scripts/gen_proto.sh
#
# Результат: packages/corag-contracts/src/corag_contracts/rag/v1/
#   rag_pb2.py       — классы сообщений
#   rag_pb2.pyi      — типы для IDE и mypy
#   rag_pb2_grpc.py  — RagServiceStub (клиент) и RagServiceServicer (сервер)
# Сгенерированный код коммитится; руками его не правим.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=$ROOT/packages/corag-contracts/src/corag_contracts/rag/v1

mkdir -p "$OUT"
cd "$ROOT"
uv run --no-sync python -m grpc_tools.protoc \
    -I proto \
    --python_out="$OUT" \
    --pyi_out="$OUT" \
    --grpc_python_out="$OUT" \
    proto/rag/v1/rag.proto

# protoc кладёт файлы по пути proto-файла (rag/v1/…) и импортирует rag_pb2 абсолютно:
#   from rag.v1 import rag_pb2
# В пакете corag_contracts такого модуля нет — переписываем импорт на полный путь.
mv "$OUT"/rag/v1/rag_pb2* "$OUT"/
rm -rf "$OUT/rag"
sed -i 's/^from rag\.v1 import rag_pb2/from corag_contracts.rag.v1 import rag_pb2/' "$OUT/rag_pb2_grpc.py"

for pkg in "$OUT/.." "$OUT"; do
    [ -f "$pkg/__init__.py" ] || echo '"""Сгенерировано scripts/gen_proto.sh из proto/rag/v1/rag.proto."""' > "$pkg/__init__.py"
done

echo "сгенерировано в ${OUT#"$ROOT"/}: $(cd "$OUT" && echo rag_pb2*)"
