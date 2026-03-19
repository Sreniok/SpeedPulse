"""Authentication, registration, and password recovery routes."""

from __future__ import annotations

import hmac
import logging
import secrets
import time
from collections.abc import Callable
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer


def build_auth_router(
    *,
    templates: Jinja2Templates,
    flash_cookie: str,
    session_cookie: str,
    get_serializer: Callable[[], URLSafeSerializer],
    is_setup_mode: Callable[[], bool],
    resolve_recovery_email: Callable[[], str],
    normalize_email: Callable[[object], str],
    extract_client_ip: Callable[[Request], str],
    is_login_blocked: Callable[[str], int],
    verify_login_credentials: Callable[[str, str], bool],
    register_failed_login: Callable[[str], int],
    clear_failed_logins: Callable[[str], None],
    env_int: Callable[[str, int], int],
    get_session_version: Callable[[], int],
    is_secure_request: Callable[[Request], bool],
    is_valid_email: Callable[[object], bool],
    build_password_hash: Callable[[str], str],
    update_env_file: Callable[[dict[str, str]], None],
    apply_runtime_env: Callable[[dict[str, str]], None],
    logger: logging.Logger,
    create_reset_token: Callable[[str], str],
    send_reset_email: Callable[[str, str, str], None],
    resolve_login_email: Callable[[], str],
    consume_reset_token: Callable[[str], str | None],
    rotate_session_version: Callable[[], int],
) -> APIRouter:
    router = APIRouter()

    def set_flash(response: RedirectResponse, message: str, path: str = "/login") -> RedirectResponse:
        signed = get_serializer().dumps({"msg": message, "t": int(time.time())})
        response.set_cookie(
            key=flash_cookie,
            value=signed,
            httponly=True,
            samesite="strict",
            max_age=60,
            path=path,
        )
        return response

    def consume_flash(request: Request) -> str | None:
        token = request.cookies.get(flash_cookie)
        if not token:
            return None
        try:
            payload = get_serializer().loads(token)
        except BadSignature:
            return None
        issued = payload.get("t", 0)
        if int(time.time()) - int(issued) > 60:
            return None
        return str(payload.get("msg", ""))

    def issue_session_cookie(response: RedirectResponse, request: Request, login_email: str) -> RedirectResponse:
        ttl_seconds = env_int("SESSION_TTL_SECONDS", 60 * 60 * 12)
        exp_ts = int(time.time()) + ttl_seconds
        csrf_token = secrets.token_urlsafe(24)
        token = get_serializer().dumps(
            {
                "login_email": login_email,
                "username": login_email,
                "exp": exp_ts,
                "csrf": csrf_token,
                "sv": get_session_version(),
            }
        )
        response.set_cookie(
            key=session_cookie,
            value=token,
            httponly=True,
            samesite="strict",
            secure=is_secure_request(request),
            max_age=ttl_seconds,
        )
        return response

    @router.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        setup_mode = is_setup_mode()
        recovery_email = resolve_recovery_email()
        error = consume_flash(request)
        response = templates.TemplateResponse(
            request,
            "login.html",
            {
                "request": request,
                "error": error,
                "setup_mode": setup_mode,
                "has_recovery_email": bool(recovery_email) and not setup_mode,
            },
        )
        response.delete_cookie(flash_cookie, path="/login")
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @router.post("/login")
    def login(
        request: Request,
        email: str = Form(""),
        username: str = Form(""),
        password: str = Form(...),
    ) -> RedirectResponse:
        if is_setup_mode():
            return RedirectResponse(url="/register", status_code=status.HTTP_302_FOUND)

        login_email = normalize_email(email or username)
        client_ip = extract_client_ip(request)
        blocked_for = is_login_blocked(client_ip)
        if blocked_for > 0:
            response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
            return set_flash(response, f"Too many attempts. Retry in {blocked_for}s")

        if not verify_login_credentials(login_email, password):
            new_block_seconds = register_failed_login(client_ip)
            if new_block_seconds > 0:
                logger.warning("Login blocked for %ss from IP %s", new_block_seconds, client_ip)
                response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
                return set_flash(response, f"Too many attempts. Retry in {new_block_seconds}s")

            logger.warning("Failed login attempt from IP %s", client_ip)
            response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
            return set_flash(response, "Invalid credentials")

        clear_failed_logins(client_ip)
        response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
        return issue_session_cookie(response, request, login_email)

    @router.get("/logout")
    def logout() -> RedirectResponse:
        response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
        response.delete_cookie(session_cookie)
        return response

    @router.get("/register", response_class=HTMLResponse)
    def register_page(request: Request):
        if not is_setup_mode():
            return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

        error = consume_flash(request)
        response = templates.TemplateResponse(request, "register.html", {"request": request, "error": error})
        response.delete_cookie(flash_cookie, path="/register")
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response

    @router.post("/register")
    def register(
        request: Request,
        email: str = Form(""),
        username: str = Form(""),
        password: str = Form(...),
        confirm_password: str = Form(...),
    ) -> RedirectResponse:
        if not is_setup_mode():
            return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

        login_email = normalize_email(email or username)
        if not is_valid_email(login_email):
            response = RedirectResponse(url="/register", status_code=status.HTTP_302_FOUND)
            return set_flash(response, "Enter a valid login email address", path="/register")

        if len(password) < 10:
            response = RedirectResponse(url="/register", status_code=status.HTTP_302_FOUND)
            return set_flash(response, "Password must be at least 10 characters", path="/register")

        if password != confirm_password:
            response = RedirectResponse(url="/register", status_code=status.HTTP_302_FOUND)
            return set_flash(response, "Passwords do not match", path="/register")

        password_hash = build_password_hash(password)
        env_updates = {
            "DASHBOARD_LOGIN_EMAIL": login_email,
            "DASHBOARD_USERNAME": "",
            "RECOVERY_EMAIL": login_email,
            "DASHBOARD_PASSWORD_HASH": password_hash,
            "DASHBOARD_PASSWORD": "",
        }

        try:
            update_env_file(env_updates)
        except OSError as exc:
            logger.error("Failed to write credentials to .env: %s", exc)
            response = RedirectResponse(url="/register", status_code=status.HTTP_302_FOUND)
            return set_flash(response, "Failed to save credentials", path="/register")

        apply_runtime_env(env_updates)
        logger.info("Account created for login email '%s' via setup wizard", login_email)

        response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
        return issue_session_cookie(response, request, login_email)

    @router.get("/forgot-password", response_class=HTMLResponse)
    def forgot_password_page(request: Request):
        if is_setup_mode():
            return RedirectResponse(url="/register", status_code=status.HTTP_302_FOUND)

        error = consume_flash(request)
        response = templates.TemplateResponse(
            request,
            "forgot_password.html",
            {"request": request, "error": error, "sent": False},
        )
        response.delete_cookie(flash_cookie, path="/forgot-password")
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response

    @router.post("/forgot-password")
    def forgot_password(request: Request, email: str = Form(""), username: str = Form("")):
        if is_setup_mode():
            return RedirectResponse(url="/register", status_code=status.HTTP_302_FOUND)

        login_email = normalize_email(email or username)
        client_ip = extract_client_ip(request)
        blocked_for = is_login_blocked(client_ip)
        if blocked_for > 0:
            return templates.TemplateResponse(
                request,
                "forgot_password.html",
                {"request": request, "error": f"Too many attempts. Retry in {blocked_for}s", "sent": False},
            )

        success_response = templates.TemplateResponse(
            request,
            "forgot_password.html",
            {"request": request, "error": None, "sent": True},
        )

        recovery_email = resolve_recovery_email()
        expected_login_email = resolve_login_email()

        if not recovery_email or not hmac.compare_digest(login_email, expected_login_email):
            register_failed_login(client_ip)
            logger.info("Forgot-password request — no action (email mismatch or no recovery email)")
            return success_response

        try:
            token = create_reset_token(login_email)
            base_url = str(request.base_url)
            send_reset_email(recovery_email, token, base_url)
            logger.info("Password reset email sent for login email '%s'", login_email)
        except Exception as exc:
            logger.error("Failed to send password reset email: %s", exc)

        return success_response

    @router.get("/reset-password", response_class=HTMLResponse)
    def reset_password_page(request: Request):
        if is_setup_mode():
            return RedirectResponse(url="/register", status_code=status.HTTP_302_FOUND)

        token = request.query_params.get("token", "")
        error = consume_flash(request)
        response = templates.TemplateResponse(
            request,
            "reset_password.html",
            {"request": request, "token": token, "error": error, "success": False},
        )
        response.delete_cookie(flash_cookie, path="/reset-password")
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response

    @router.post("/reset-password")
    def reset_password(
        request: Request,
        token: str = Form(...),
        new_password: str = Form(...),
        confirm_password: str = Form(...),
    ):
        if is_setup_mode():
            return RedirectResponse(url="/register", status_code=status.HTTP_302_FOUND)

        if len(new_password) < 10:
            response = RedirectResponse(url=f"/reset-password?token={quote(token)}", status_code=status.HTTP_302_FOUND)
            return set_flash(response, "Password must be at least 10 characters", path="/reset-password")

        if new_password != confirm_password:
            response = RedirectResponse(url=f"/reset-password?token={quote(token)}", status_code=status.HTTP_302_FOUND)
            return set_flash(response, "Passwords do not match", path="/reset-password")

        login_email = consume_reset_token(token)
        if not login_email:
            return templates.TemplateResponse(
                request,
                "reset_password.html",
                {
                    "request": request,
                    "token": "",
                    "error": "Reset link is invalid or has expired. Please request a new one.",
                    "success": False,
                },
            )

        new_hash = build_password_hash(new_password)
        env_updates = {"DASHBOARD_PASSWORD_HASH": new_hash, "DASHBOARD_PASSWORD": ""}

        try:
            update_env_file(env_updates)
        except OSError as exc:
            logger.error("Failed to persist password reset: %s", exc)
            return templates.TemplateResponse(
                request,
                "reset_password.html",
                {"request": request, "token": "", "error": "Failed to save new password.", "success": False},
            )

        apply_runtime_env(env_updates)
        rotate_session_version()
        logger.info("Password reset completed for login email '%s' — all sessions invalidated", login_email)

        return templates.TemplateResponse(
            request,
            "reset_password.html",
            {"request": request, "token": "", "error": None, "success": True},
        )

    return router
