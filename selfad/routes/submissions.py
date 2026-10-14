from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from selfad.auth import get_session_user
from selfad.branding import get_branding_context
from selfad.contest import ENDED, STARTED, as_utc, contest_state, start_contest_if_due
from selfad.database import get_session
from selfad.models import InstanceConfig
from selfad.scoring import MAX_ATTEMPT_HISTORY_PER_SERVICE
from selfad.submissions import STATUS_LABELS, read_history
from selfad.web import templates

router = APIRouter()


def positive_int(value: str, default: int = 1) -> int:
    try:
        return max(1, int(value))
    except ValueError:
        return default


@router.get("/submissions", response_class=HTMLResponse)
def submissions(request: Request, session: Session = Depends(get_session)):
    branding = get_branding_context(session)
    if not branding["is_configured"]:
        return RedirectResponse("/setup", status_code=303)
    user = get_session_user(request, session)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    config = session.get(InstanceConfig, 1)
    if start_contest_if_due(config):
        session.commit()
        branding = get_branding_context(session)
    if not user.is_admin and contest_state(config) not in {STARTED, ENDED}:
        return RedirectResponse("/", status_code=303)
    params = request.query_params
    participant = params.get("participant", "").strip()[:32] if user.is_admin else ""
    kind = params.get("kind", "")
    kind = kind if kind in {"attack", "defense"} else ""
    status = params.get("status", "")
    status = status if status in STATUS_LABELS else ""
    service_id = positive_int(params.get("service", ""), 0) or None
    rows, services, total, page, pages = read_history(
        session, user_id=None if user.is_admin else user.id,
        participant=participant, kind=kind, status=status,
        service_id=service_id, page=positive_int(params.get("page", "1")),
    )
    rows = [{**row, "recorded_at": as_utc(row["recorded_at"])} for row in rows]
    filters = {"participant": participant, "kind": kind, "status": status,
               "service": str(service_id) if service_id else ""}

    def page_url(number):
        return "/submissions?" + urlencode({
            **{key: value for key, value in filters.items() if value}, "page": number,
        })

    return templates.TemplateResponse(request=request, name="submissions.html", context={
        "title": f"Submissions · {branding['brand_title']}", "current_user": user,
        "rows": rows, "services": services, "total": total, "page": page,
        "pages": pages, "previous_url": page_url(page - 1), "next_url": page_url(page + 1),
        "filters": filters, "status_labels": STATUS_LABELS,
        "retention_limit": MAX_ATTEMPT_HISTORY_PER_SERVICE, **branding,
    })
