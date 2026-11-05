"""Вопросы к архиву: REST → gRPC-вызов RAG Service (Ask, Search)."""

from fastapi import APIRouter, Depends

from corag_api.auth.dependencies import require_researcher
from corag_api.deps import RagServiceDep
from corag_api.errors import AUTH_RESPONSES, RAG_RESPONSES, ErrorOut
from corag_api.schemas.rag import AskIn, AskOut, CitationOut, SearchIn, SearchOut

router = APIRouter(
    prefix="/rag",
    tags=["rag"],
    dependencies=[Depends(require_researcher)],
    responses={
        **AUTH_RESPONSES,
        422: {"model": ErrorOut, "description": "Некорректный запрос (в том числе от RAG Service)"},
        **RAG_RESPONSES,
    },
)


@router.post("/ask", response_model=AskOut, summary="Вопрос к архиву: ответ LLM с источниками")
async def ask(data: AskIn, service: RagServiceDep) -> AskOut:
    """Наивный RAG: эмбеддинг вопроса → top-k фрагментов (pgvector) → ответ локальной LLM.

    Если ни один фрагмент не прошёл порог сходства, ответ «в архиве не найдено» и `found=false`
    (LLM не вызывается).
    """
    return AskOut.model_validate(await service.ask(data), from_attributes=True)


@router.post("/search", response_model=SearchOut, summary="Поиск фрагментов без генерации")
async def search(data: SearchIn, service: RagServiceDep) -> SearchOut:
    results = await service.search(data)
    return SearchOut(results=[CitationOut.model_validate(c, from_attributes=True) for c in results])
