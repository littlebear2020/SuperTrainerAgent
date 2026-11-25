from fastapi import APIRouter, HTTPException, Request

from app.gateway.authz import AuthContext, require_permission
from app.gateway.deps import get_checkpointer, get_thread_store
from app.gateway.works.schemas import ArtifactSummariesRequest, ThreadArtifactSummary
from app.gateway.works.service import load_thread_artifact_summary

router = APIRouter(prefix="/api/works", tags=["works"])


@router.post("/artifact-summaries", response_model=list[ThreadArtifactSummary])
@require_permission("threads", "read")
async def artifact_summaries(body: ArtifactSummariesRequest, request: Request) -> list[ThreadArtifactSummary]:
    auth: AuthContext = request.state.auth
    user = auth.require_user()
    thread_ids = list(dict.fromkeys(body.thread_ids))
    thread_store = get_thread_store(request)

    for thread_id in thread_ids:
        allowed = await thread_store.check_access(thread_id, str(user.id), require_existing=True)
        if not allowed:
            raise HTTPException(status_code=404, detail="Thread not found")

    checkpointer = get_checkpointer(request)
    summaries: list[ThreadArtifactSummary] = []
    for thread_id in thread_ids:
        summaries.append(await load_thread_artifact_summary(thread_id, checkpointer))
    return summaries
