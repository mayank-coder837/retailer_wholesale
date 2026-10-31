"""The request's transaction commits before the response is sent (found by CI: a page asked for the document
it had just posted and got 404, because the commit came after the 201)."""

from collections.abc import AsyncIterator
from typing import Annotated, Any, get_args

from fastapi import FastAPI
from fastapi.params import Depends
from httpx import ASGITransport, AsyncClient

from app.core.deps import TenantSessionDep, get_tenant_session


def test_the_tenant_session_ends_with_the_endpoint_not_after_the_response() -> None:
    marker = next(m for m in get_args(TenantSessionDep)[1:] if isinstance(m, Depends))
    assert marker.dependency is get_tenant_session
    assert marker.scope == "function"


async def test_function_scope_commits_before_the_response_leaves() -> None:
    # The same FastAPI mechanism on a stand-in unit of work: with scope="function" its code after yield (our
    # commit) runs before the response starts; with the default it runs after.
    events: list[str] = []

    async def unit_of_work() -> AsyncIterator[None]:
        yield
        events.append("committed")

    inner = FastAPI()

    @inner.post("/function")
    async def function_scope(_: Annotated[None, Depends(unit_of_work, scope="function")]) -> None:
        return None

    @inner.post("/request")
    async def request_scope(_: Annotated[None, Depends(unit_of_work)]) -> None:
        return None

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        async def recording(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                events.append("sent")
            await send(message)

        await inner(scope, receive, recording)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:  # type: ignore[arg-type]
        await client.post("/request")
        assert events == ["sent", "committed"]  # the race CI hit
        events.clear()
        await client.post("/function")
        assert events == ["committed", "sent"]
