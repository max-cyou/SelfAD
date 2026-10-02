from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from selfad.auth import csrf_token_is_valid, get_session_user
from selfad.database import get_session
from selfad.gitea import (
    GiteaConflict,
    GiteaError,
    GiteaUnavailable,
    add_user_ssh_key,
    create_gitea_user,
    delete_gitea_user,
    delete_repository,
    delete_user_ssh_key,
    gitea_username_exists,
    update_gitea_user,
)
from selfad.models import ParticipantService, User
from selfad.routes.admin_shared import (
    _users_admin_url,
    get_admin_access,
    render_admin,
    validate_participant_form,
)
from selfad.security import hash_password
from selfad.settings import (
    get_gitea_root_password,
    get_gitea_settings,
    set_gitea_root_password,
)


router = APIRouter()


@router.post("/admin/gitea-credentials")
async def gitea_credentials(
    request: Request,
    session: Session = Depends(get_session),
):
    _, redirect = get_admin_access(request, session)
    if redirect:
        raise HTTPException(status_code=403, detail="Administrator access required.")

    form = await request.form()
    if not csrf_token_is_valid(request, form.get("csrf_token")):
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")

    password = get_gitea_root_password()
    if password is None:
        raise HTTPException(status_code=503, detail="Gitea password is unavailable.")

    return JSONResponse(
        {"username": "root", "password": password},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/admin/participants")
async def create_participant(
    request: Request,
    session: Session = Depends(get_session),
):
    _, redirect = get_admin_access(request, session)
    if redirect:
        return redirect
    form = await request.form()
    if not csrf_token_is_valid(request, form.get("csrf_token")):
        return HTMLResponse("Invalid CSRF token.", status_code=403)
    values = {
        "username": str(form.get("username", "")).strip(),
        "email": str(form.get("email", "")).strip().lower(),
        "ssh_public_key": str(form.get("ssh_public_key", "")).strip(),
        "role": str(form.get("role", "user")).strip().lower(),
    }
    password = str(form.get("password", ""))
    errors = validate_participant_form(values, password)
    if session.scalar(select(User.id).where(User.username == values["username"])):
        errors["username"] = "This participant already exists."
    if session.scalar(select(User.id).where(User.email == values["email"])):
        errors["email"] = "This email is already in use."
    settings = get_gitea_settings()
    if not errors:
        try:
            taken_in_gitea = await run_in_threadpool(
                gitea_username_exists,
                settings,
                username=values["username"],
            )
        except GiteaUnavailable as error:
            return render_admin(request, session, get_session_user(request, session), participant_errors={"_form": str(error)}, participant_form=values, active_section="users", status_code=502)
        if taken_in_gitea:
            errors["username"] = "This username is already in use."
    if errors:
        return render_admin(request, session, get_session_user(request, session), participant_errors=errors, participant_form=values, active_section="users", status_code=422)

    gitea_user = None
    try:
        gitea_user = await run_in_threadpool(create_gitea_user, settings, username=values["username"], email=values["email"], password=password)
        ssh_key_id = await run_in_threadpool(add_user_ssh_key, settings, username=gitea_user.username, public_key=values["ssh_public_key"])
    except GiteaConflict as error:
        if gitea_user is not None:
            try:
                await run_in_threadpool(delete_gitea_user, settings, username=gitea_user.username)
            except GiteaError:
                pass
        return render_admin(request, session, get_session_user(request, session), participant_errors={"username" if gitea_user is None else "ssh_public_key": str(error)}, participant_form=values, active_section="users", status_code=422)
    except GiteaUnavailable as error:
        if gitea_user is not None:
            try:
                await run_in_threadpool(delete_gitea_user, settings, username=gitea_user.username)
            except GiteaError:
                pass
        return render_admin(request, session, get_session_user(request, session), participant_errors={"_form": str(error)}, participant_form=values, active_section="users", status_code=502)

    participant = User(username=values["username"], email=values["email"], password_hash=hash_password(password), is_admin=values["role"] == "admin", ssh_public_key=values["ssh_public_key"], git_ssh_key_id=ssh_key_id, gitea_user_id=gitea_user.id, gitea_username=gitea_user.username)
    session.add(participant)
    session.commit()
    return RedirectResponse(url=_users_admin_url(request), status_code=303)


@router.post("/admin/users/{user_id}", response_class=HTMLResponse)
async def update_user(
    user_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    current_user, redirect = get_admin_access(request, session)
    if redirect:
        return redirect
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")

    form = await request.form()
    if not csrf_token_is_valid(request, form.get("csrf_token")):
        return HTMLResponse("Invalid CSRF token.", status_code=403)
    values = {
        "username": target.username,
        "email": str(form.get("email", "")).strip().lower(),
        "ssh_public_key": str(form.get("ssh_public_key", "")).strip(),
        "role": str(form.get("role", "user")).strip().lower(),
    }
    password = str(form.get("password", ""))
    errors = validate_participant_form(values, password, editing=True)
    duplicate_email = session.scalar(
        select(User.id).where(User.email == values["email"], User.id != target.id)
    )
    if duplicate_email is not None:
        errors["email"] = "This email is already in use."
    if target.id == current_user.id and values["role"] != "admin":
        errors["role"] = "You cannot remove your own admin access."
    if errors:
        return render_admin(
            request,
            session,
            current_user,
            participant_errors=errors,
            participant_form=values,
            user_edit_id=target.id,
            active_section="users",
            status_code=422,
        )

    settings = get_gitea_settings()
    if target.gitea_username:
        try:
            await run_in_threadpool(
                update_gitea_user,
                settings,
                username=target.gitea_username,
                email=values["email"],
                password=password or None,
            )
            if target.gitea_username == "root" and password:
                await run_in_threadpool(set_gitea_root_password, password)
        except (GiteaConflict, GiteaUnavailable, OSError) as error:
            return render_admin(
                request,
                session,
                current_user,
                participant_errors={"_form": str(error)},
                participant_form=values,
                user_edit_id=target.id,
                active_section="users",
                status_code=502,
            )
    if values["ssh_public_key"] and target.gitea_username:
        if values["ssh_public_key"] != target.ssh_public_key:
            try:
                if target.git_ssh_key_id:
                    await run_in_threadpool(
                        delete_user_ssh_key,
                        settings,
                        username=target.gitea_username,
                        key_id=target.git_ssh_key_id,
                    )
                target.git_ssh_key_id = await run_in_threadpool(
                    add_user_ssh_key,
                    settings,
                    username=target.gitea_username,
                    public_key=values["ssh_public_key"],
                )
            except (GiteaConflict, GiteaUnavailable) as error:
                return render_admin(
                    request,
                    session,
                    current_user,
                    participant_errors={"_form": str(error)},
                    participant_form=values,
                    user_edit_id=target.id,
                    active_section="users",
                    status_code=502,
                )
            target.ssh_public_key = values["ssh_public_key"]

    target.email = values["email"]
    target.is_admin = values["role"] == "admin"
    if password:
        target.password_hash = hash_password(password)
    session.commit()
    return RedirectResponse(url=_users_admin_url(request), status_code=303)


@router.post("/admin/users/{user_id}/delete", response_class=HTMLResponse)
async def delete_user(
    user_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    current_user, redirect = get_admin_access(request, session)
    if redirect:
        return redirect
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")
    form = await request.form()
    if not csrf_token_is_valid(request, form.get("csrf_token")):
        return HTMLResponse("Invalid CSRF token.", status_code=403)
    if target.id == current_user.id:
        return render_admin(
            request,
            session,
            current_user,
            participant_errors={"_form": "You cannot delete the account you are using."},
            active_section="users",
            status_code=422,
        )

    assignments = session.scalars(
        select(ParticipantService).where(ParticipantService.user_id == target.id)
    ).all()
    settings = get_gitea_settings()
    try:
        for assignment in assignments:
            for repository_path in (
                assignment.attack_repository_path,
                assignment.defense_repository_path,
            ):
                await run_in_threadpool(delete_repository, settings, repository_path)
        if target.gitea_username and target.gitea_username != "root":
            await run_in_threadpool(
                delete_gitea_user,
                settings,
                username=target.gitea_username,
            )
    except GiteaError as error:
        return render_admin(
            request,
            session,
            current_user,
            participant_errors={"_form": str(error)},
            active_section="users",
            status_code=502,
        )

    for assignment in assignments:
        session.delete(assignment)
    session.delete(target)
    session.commit()
    return RedirectResponse(url=_users_admin_url(request), status_code=303)
