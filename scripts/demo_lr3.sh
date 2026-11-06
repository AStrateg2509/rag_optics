#!/usr/bin/env bash
# Демонстрация ЛР3: REST (API Service) → gRPC → RAG Service (Jina + pgvector + LLM в Ollama).
#
#   bash scripts/demo_lr3.sh                          # API напрямую: http://localhost:8000
#   bash scripts/demo_lr3.sh http://localhost/api     # через Nginx
#   bash scripts/demo_lr3.sh --expect-down            # RAG Service остановлен → 503
#
# Нужны: запущенные RAG Service и API, Ollama с моделью LLM_MODEL, сид (python -m corag_db.seed), jq.
# Путь запроса смотреть в логах двух процессов: [api] → gRPC … и [rag] ← Ask …
set -euo pipefail

EXPECT_DOWN=false
if [ "${1:-}" = "--expect-down" ]; then
    EXPECT_DOWN=true
    shift
fi
BASE=${1:-${BASE_URL:-http://localhost:8000}}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
env_value() { grep -s "^$1=" "$ROOT/.env" | tail -1 | cut -d= -f2-; }
ADMIN_PASSWORD=${SEED_ADMIN_PASSWORD:-$(env_value SEED_ADMIN_PASSWORD)}
RESEARCHER_PASSWORD=${SEED_RESEARCHER_PASSWORD:-$(env_value SEED_RESEARCHER_PASSWORD)}
MAXLINES=${MAXLINES:-14}  # сколько строк JSON ответа печатать

IN_ARCHIVE="Как рассчитать ДОЭ для фокусировки лазерного излучения в кольцо?"
OUT_OF_ARCHIVE="Как приготовить борщ?"

command -v jq >/dev/null || { echo "нужен jq: sudo apt install -y jq" >&2; exit 1; }

step() { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }

# req METHOD PATH [аргументы curl] — печатает запрос, код, время и JSON; код → $CODE, тело → $BODY
req() {
    local method=$1 path=$2 out
    shift 2
    out=$(curl -sS -X "$method" "$BASE$path" -w '\n%{http_code} %{time_total}' "$@")
    read -r CODE TIME <<<"${out##*$'\n'}"
    BODY=${out%$'\n'*}
    printf '→ %s %s\n← %s (%.1f s)\n' "$method" "$path" "$CODE" "$TIME"
    if [ -n "$BODY" ] && [ "$MAXLINES" -gt 0 ]; then
        jq . <<<"$BODY" | head -n "$MAXLINES"
        [ "$(jq . <<<"$BODY" | wc -l)" -le "$MAXLINES" ] || echo "  …"
    fi
}

expect() {
    [ "$CODE" = "$1" ] || { printf '\033[1;31mожидался %s, получен %s\033[0m\n' "$1" "$CODE" >&2; exit 1; }
}

login() {  # login EMAIL PASSWORD → печатает токен
    curl -sS -X POST "$BASE/auth/login" -d "username=$1" --data-urlencode "password=$2" \
        | jq -r .access_token
}

json() { jq -n "$@"; }

# Краткий вид ответа /rag/ask: ответ LLM и источники одной строкой каждый
show_answer() {
    jq -r '"found: \(.found)\nanswer: \(.answer)",
           (.citations | to_entries[] | "  [\(.key + 1)] \(.value.article_title // "вне статей"), "
             + "\(.value.year) № \(.value.issue_number), с. \(.value.page_number), "
             + "score \(.value.score)\(if (.value.authors | length) > 0
                                       then "; " + (.value.authors | join(", ")) else "" end)")' \
        <<<"$BODY"
}

step "0. API жив, БД доступна"
req GET /health
expect 200

step "1. Вход исследователя и администратора (сид)"
R_TOKEN=$(login researcher@corag.local "${RESEARCHER_PASSWORD:-researcher12345}")
A_TOKEN=$(login admin@corag.local "${ADMIN_PASSWORD:-admin12345}")
[ "$R_TOKEN" != null ] && [ "$A_TOKEN" != null ] || { echo "вход не удался (сид выполнен?)" >&2; exit 1; }
R_AUTH=(-H "Authorization: Bearer $R_TOKEN")
A_AUTH=(-H "Authorization: Bearer $A_TOKEN")
JSON=(-H 'Content-Type: application/json')
echo "токены получены"

if $EXPECT_DOWN; then
    step "RAG Service остановлен: gRPC UNAVAILABLE → 503"
    req POST /rag/ask "${R_AUTH[@]}" "${JSON[@]}" -d "$(json --arg q "$IN_ARCHIVE" '{question: $q}')"
    expect 503
    req POST /rag/search "${R_AUTH[@]}" "${JSON[@]}" -d '{"query": "вихревые пучки"}'
    expect 503
    printf '\n\033[1;32mRAG Service недоступен → API отвечает 503\033[0m\n'
    exit 0
fi

step "2. Переиндексация исследователем → 403"
req POST /issues/1/reindex "${R_AUTH[@]}"
expect 403

step "3. Переиндексация выпусков с текстом (gRPC IndexIssue): чанки + эмбеддинги Jina"
IDS=$(curl -sS "$BASE/issues?limit=100" "${A_AUTH[@]}" | jq '.items[].id')
WITH_TEXT=()
NO_TEXT=""
for id in $IDS; do
    pages=$(curl -sS "$BASE/issues/$id" "${A_AUTH[@]}" | jq .pages_count)
    if [ "$pages" -gt 0 ]; then WITH_TEXT+=("$id"); else NO_TEXT=${NO_TEXT:-$id}; fi
done
[ "${#WITH_TEXT[@]}" -gt 0 ] || { echo "нет выпусков с текстом страниц (сид выполнен?)" >&2; exit 1; }
for id in "${WITH_TEXT[@]}"; do
    MAXLINES=5 req POST "/issues/$id/reindex" "${A_AUTH[@]}"
    expect 200
done
FIRST=${WITH_TEXT[0]}
CHUNKS=$(jq .chunks_indexed <<<"$(curl -sS -X POST "$BASE/issues/$FIRST/reindex" "${A_AUTH[@]}")")
echo "повтор для выпуска $FIRST: $CHUNKS чанков; в карточке: $(curl -sS "$BASE/issues/$FIRST" \
    "${A_AUTH[@]}" | jq -c '{status, chunks_count}') — дублей нет"

step "4. Ошибки IndexIssue: нет выпуска → 404 (NOT_FOUND), нет текста → 409 (FAILED_PRECONDITION)"
req POST /issues/999999/reindex "${A_AUTH[@]}"
expect 404
if [ -n "$NO_TEXT" ]; then
    req POST "/issues/$NO_TEXT/reindex" "${A_AUTH[@]}"
    expect 409
fi

step "5. Поиск фрагментов без LLM: POST /rag/search (gRPC Search)"
MAXLINES=16 req POST /rag/search "${R_AUTH[@]}" "${JSON[@]}" \
    -d '{"query": "вихревые лазерные пучки", "top_k": 3}'
expect 200

step "6. Вопрос по архиву: POST /rag/ask (gRPC Ask → эмбеддинг → pgvector → LLM)"
MAXLINES=0 req POST /rag/ask "${R_AUTH[@]}" "${JSON[@]}" \
    -d "$(json --arg q "$IN_ARCHIVE" '{question: $q, top_k: 3}')"
expect 200
show_answer
[ "$(jq .found <<<"$BODY")" = true ] || { echo "ожидался found=true" >&2; exit 1; }

step "7. Фильтр по годам: только 2015–2024"
MAXLINES=0 req POST /rag/ask "${R_AUTH[@]}" "${JSON[@]}" \
    -d '{"question": "Что такое топологический заряд вихревого пучка?", "year_from": 2015, "year_to": 2024}'
expect 200
show_answer

step "8. Вопрос вне архива → found=false, LLM не вызывается"
req POST /rag/ask "${R_AUTH[@]}" "${JSON[@]}" -d "$(json --arg q "$OUT_OF_ARCHIVE" '{question: $q}')"
expect 200
[ "$(jq .found <<<"$BODY")" = false ] || { echo "ожидался found=false" >&2; exit 1; }

step "9. Пустой вопрос → 422 (до gRPC не доходит)"
MAXLINES=6 req POST /rag/ask "${R_AUTH[@]}" "${JSON[@]}" -d '{"question": "   "}'
expect 422

printf '\n\033[1;32mДемо ЛР3 пройдено: проиндексировано выпусков — %s\033[0m\n' "${#WITH_TEXT[@]}"
