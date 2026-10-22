#!/usr/bin/env bash
# Демонстрация ЛР2: работа с БД только через REST API (curl + jq).
#
#   bash scripts/demo_lr2.sh                        # API напрямую: http://localhost:8000
#   bash scripts/demo_lr2.sh http://localhost/api   # через Nginx
#
# Нужны: запущенный API, сид (python -m corag_db.seed), jq, uv (генерация тестового PDF).
# Скрипт можно запускать повторно: e-mail и номер выпуска каждый раз новые.
set -euo pipefail

BASE=${1:-${BASE_URL:-http://localhost:8000}}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
env_value() { grep -s "^$1=" "$ROOT/.env" | tail -1 | cut -d= -f2-; }
ADMIN_EMAIL=admin@corag.local
ADMIN_PASSWORD=${SEED_ADMIN_PASSWORD:-$(env_value SEED_ADMIN_PASSWORD)}
ADMIN_PASSWORD=${ADMIN_PASSWORD:-admin12345}
PDF=$ROOT/data/demo/scan.pdf
MAXLINES=${MAXLINES:-14}  # сколько строк JSON ответа печатать

command -v jq >/dev/null || { echo "нужен jq: sudo apt install -y jq" >&2; exit 1; }

step() { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }

# req METHOD PATH [аргументы curl] — печатает запрос, код и JSON ответа; код → $CODE, тело → $BODY
req() {
    local method=$1 path=$2 out
    shift 2
    out=$(curl -sS -X "$method" "$BASE$path" -w '\n%{http_code}' "$@")
    CODE=${out##*$'\n'}
    BODY=${out%$'\n'*}
    printf '→ %s %s\n← %s\n' "$method" "$path" "$CODE"
    if [ -n "$BODY" ]; then
        jq . <<<"$BODY" | head -n "$MAXLINES"
        [ "$(jq . <<<"$BODY" | wc -l)" -le "$MAXLINES" ] || echo "  …"
    fi
}

# expect CODE — остановить демо, если код ответа не тот
expect() {
    [ "$CODE" = "$1" ] || { printf '\033[1;31mожидался %s, получен %s\033[0m\n' "$1" "$CODE" >&2; exit 1; }
}

# Полезная нагрузка JWT (base64url → JSON)
jwt_payload() {
    local p
    p=$(cut -d. -f2 <<<"$1" | tr '_-' '/+')
    while (( ${#p} % 4 )); do p+="="; done
    base64 -d <<<"$p" | jq -c .
}

login() {  # login EMAIL PASSWORD → печатает токен
    curl -sS -X POST "$BASE/auth/login" -d "username=$1" --data-urlencode "password=$2" \
        | jq -r .access_token
}

step "0. API жив, БД доступна"
req GET /health
expect 200

step "1. Регистрация исследователя (роль назначается сервером) и повтор → 409"
EMAIL="demo.$(date +%s)@example.com"
USER_JSON=$(jq -n --arg e "$EMAIL" '{email: $e, password: "demo-pass-123", full_name: "Демо Исследователь"}')
req POST /auth/register -H 'Content-Type: application/json' -d "$USER_JSON"
expect 201
req POST /auth/register -H 'Content-Type: application/json' -d "$USER_JSON"
expect 409

step "2. Вход (OAuth2 Password, form-urlencoded) → JWT"
req POST /auth/login -d "username=$EMAIL" -d "password=demo-pass-123"
expect 200
R_TOKEN=$(jq -r .access_token <<<"$BODY")
echo "payload: $(jwt_payload "$R_TOKEN")"
R_AUTH=(-H "Authorization: Bearer $R_TOKEN")

step "3. Запрос с токеном: GET /users/me"
req GET /users/me "${R_AUTH[@]}"
expect 200

step "4. Без токена → 401 + WWW-Authenticate"
req GET /issues
expect 401
curl -sS -o /dev/null -D - "$BASE/issues" | grep -i '^www-authenticate'

step "5. Исследователь создаёт выпуск → 403"
req POST /issues "${R_AUTH[@]}" -H 'Content-Type: application/json' -d '{"year": 2026, "number": 1}'
expect 403

step "6. Каталог (исследователь): выпуски 2015 года, статьи автора"
req GET "/issues?year=2015" "${R_AUTH[@]}"
expect 200
MAXLINES=6 req GET "/articles?author=%D0%9A%D1%83%D0%B7%D0%BD%D0%B5%D1%86%D0%BE%D0%B2&limit=2" "${R_AUTH[@]}"
expect 200
echo "статей Кузнецова: $(jq .total <<<"$BODY"); на странице: $(jq -c '[.items[].title]' <<<"$BODY")"

step "7. Вход администратора"
A_TOKEN=$(login "$ADMIN_EMAIL" "$ADMIN_PASSWORD")
[ "$A_TOKEN" != null ] || { echo "не удалось войти как $ADMIN_EMAIL (сид выполнен?)" >&2; exit 1; }
echo "payload: $(jwt_payload "$A_TOKEN")"
A_AUTH=(-H "Authorization: Bearer $A_TOKEN")

step "8. Создание выпуска (admin) → 201, статус created"
LAST=$(curl -sS "$BASE/issues?year=2026&limit=100" "${A_AUTH[@]}" | jq '[.items[].number] | max // 0')
NUMBER=$((LAST + 1))
req POST /issues "${A_AUTH[@]}" -H 'Content-Type: application/json' \
    -d "{\"year\": 2026, \"volume\": 50, \"number\": $NUMBER}"
expect 201
ISSUE_ID=$(jq .id <<<"$BODY")

step "9. Загрузка PDF-скана (multipart) → статус uploaded, число страниц"
uv run --no-sync python "$ROOT/scripts/make_test_pdf.py" "$PDF" --pages 8
req POST "/issues/$ISSUE_ID/scan" "${A_AUTH[@]}" -F "file=@$PDF;type=application/pdf"
expect 200
echo "не PDF:"
req POST "/issues/$ISSUE_ID/scan" "${A_AUTH[@]}" -F "file=@$ROOT/pyproject.toml;type=application/pdf;filename=fake.pdf"
expect 422

step "10. Автор: поиск, иначе создание"
ORLOVA=$(curl -sS -G "$BASE/authors" --data-urlencode "q=Орлова" "${A_AUTH[@]}" | jq '.items[0].id')
req GET "/authors?q=%D0%94%D0%B5%D0%BC%D0%BE" "${A_AUTH[@]}"
AUTHOR_ID=$(jq '.items[0].id // empty' <<<"$BODY")
if [ -z "$AUTHOR_ID" ]; then
    req POST /authors "${A_AUTH[@]}" -H 'Content-Type: application/json' \
        -d '{"full_name": "Демо Автор Петрович", "affiliation": "Самарский университет"}'
    expect 201
    AUTHOR_ID=$(jq .id <<<"$BODY")
fi

step "11. Статья с авторами (порядок) и ключевыми словами → 201"
ARTICLE=$(jq -n --argjson i "$ISSUE_ID" --argjson a "$AUTHOR_ID" --argjson o "$ORLOVA" \
    '{issue_id: $i, title: "Демо: расчёт дифракционного элемента", page_start: 2, page_end: 5,
      author_ids: [$a, $o], keywords: ["ДОЭ", " Дифракция "], language: "ru"}')
MAXLINES=40 req POST /articles "${A_AUTH[@]}" -H 'Content-Type: application/json' -d "$ARTICLE"
expect 201
ARTICLE_ID=$(jq .id <<<"$BODY")

step "12. Диапазон страниц за пределами выпуска (8 стр.) → 422"
req POST /articles "${A_AUTH[@]}" -H 'Content-Type: application/json' \
    -d "$(jq '.page_end = 20' <<<"$ARTICLE")"
expect 422

step "13. Фильтры каталога: автор + ключевое слово, выпуск"
MAXLINES=4 req GET "/articles?author=%D0%9E%D1%80%D0%BB%D0%BE%D0%B2%D0%B0&keyword=%D0%B4%D0%BE%D1%8D" "${R_AUTH[@]}"
expect 200
echo "найдено: $(jq -c '[.items[] | {id, title, authors: [.authors[].full_name]}]' <<<"$BODY")"
MAXLINES=4 req GET "/articles?issue_id=$ISSUE_ID" "${R_AUTH[@]}"
expect 200

step "14. Исправление статьи (PATCH) → 200"
req PATCH "/articles/$ARTICLE_ID" "${A_AUTH[@]}" -H 'Content-Type: application/json' \
    -d '{"keywords": ["доэ", "голография"], "page_end": 6}'
expect 200
echo "keywords: $(jq -c .keywords <<<"$BODY"), page_end: $(jq .page_end <<<"$BODY")"

step "15. Запуск OCR → 202 Accepted (задача в очереди), повтор → 409"
req POST "/issues/$ISSUE_ID/ocr" "${A_AUTH[@]}"
expect 202
JOB_ID=$(jq .id <<<"$BODY")
req POST "/issues/$ISSUE_ID/ocr" "${A_AUTH[@]}"
expect 409

step "16. Статус задачи OCR; исследователю — 403"
req GET "/ocr-jobs/$JOB_ID" "${A_AUTH[@]}"
expect 200
req GET "/ocr-jobs/$JOB_ID" "${R_AUTH[@]}"
expect 403

step "17. Карточка выпуска: статус и счётчики"
req GET "/issues/$ISSUE_ID" "${R_AUTH[@]}"
expect 200

printf '\n\033[1;32mДемо ЛР2 пройдено: выпуск %s (2026 №%s), статья %s, задача OCR %s\033[0m\n' \
    "$ISSUE_ID" "$NUMBER" "$ARTICLE_ID" "$JOB_ID"
