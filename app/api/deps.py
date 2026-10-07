"""FastAPI dependencies. Long-lived objects are built once in ``create_app``
and stored on ``app.state``; per-request objects are built here."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Query, Request
from sqlalchemy.orm import Session

from app.db.database import session_scope
from app.services.file_service import FileService

MAX_PAGE_SIZE = 1000


def get_session(request: Request) -> Iterator[Session]:
    yield from session_scope(request.app.state.session_factory)


def get_file_service(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> FileService:
    return FileService(session, request.app.state.settings, request.app.state.processing_service)


class PageParams:
    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, description="Page size.")] = 100,
        offset: Annotated[int, Query(ge=0, description="Items to skip.")] = 0,
    ) -> None:
        self.limit = limit
        self.offset = offset


FileServiceDep = Annotated[FileService, Depends(get_file_service)]
PageDep = Annotated[PageParams, Depends()]
