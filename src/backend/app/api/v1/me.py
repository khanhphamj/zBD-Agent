from typing import Any

from fastapi import APIRouter, Request

from app.api.deps import CurrentUser, SessionDep
from app.core.text import initials, normalize
from app.db.models.user import User, UserNotificationSettings
from app.schemas.requests import (
    NotificationSettingsPatch,
    OnboardingPatch,
    ProfilePatch,
)
from app.schemas.serializers import me as serialize_me
from app.schemas.serializers import notification_settings as serialize_settings
from app.schemas.serializers import profile as serialize_profile
from app.services import audit
from app.services.auth import permissions_for

router = APIRouter(tags=["me"])


@router.get("/me", summary="Hồ sơ người dùng hiện tại")
async def read_me(user: CurrentUser, session: SessionDep) -> dict[str, Any]:
    return serialize_me(user, await permissions_for(session, user))


@router.get("/me/onboarding", summary="Tiến độ onboarding và tour")
async def read_onboarding(user: CurrentUser) -> dict[str, Any]:
    return {
        "completedSteps": user.onboarding_steps,
        "requiredSteps": list(User.ONBOARDING_STEPS),
        "tourCompleted": user.tour_completed,
        "isFirstLogin": user.is_first_login,
        "needsOnboarding": user.needs_onboarding,
        "needsTour": user.needs_tour,
        "interests": user.interests,
    }


@router.patch("/me/onboarding", summary="Lưu tiến độ onboarding")
async def update_onboarding(
    body: OnboardingPatch, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    if body.completedSteps is not None:
        merged = list(dict.fromkeys([*user.onboarding_steps, *body.completedSteps]))
        user.onboarding_steps = merged
    if body.tourCompleted is not None:
        user.tour_completed = body.tourCompleted
    if body.interests is not None:
        user.interests = body.interests

    await session.commit()
    return {
        "completedSteps": user.onboarding_steps,
        "requiredSteps": list(User.ONBOARDING_STEPS),
        "tourCompleted": user.tour_completed,
        "needsOnboarding": user.needs_onboarding,
        "needsTour": user.needs_tour,
        "interests": user.interests,
    }


@router.get("/me/profile", summary="Chi tiết hồ sơ")
async def read_profile(user: CurrentUser) -> dict[str, Any]:
    return serialize_profile(user)


@router.patch("/me/profile", summary="Cập nhật hồ sơ")
async def update_profile(
    body: ProfilePatch, user: CurrentUser, session: SessionDep, request: Request
) -> dict[str, Any]:
    if body.name is not None:
        user.name = body.name
        user.name_norm = normalize(body.name)
        user.initials = initials(body.name)
    for field, value in (
        ("title", body.title),
        ("phone", body.phone),
        ("team_id", body.teamId),
        ("avatar_color", body.avatarColor),
        ("interests", body.interests),
    ):
        if value is not None:
            setattr(user, field, value)

    await audit.record(
        session,
        action="user.profile_update",
        summary=f"{user.name} cập nhật hồ sơ cá nhân",
        actor=user,
        resource_type="user",
        resource_id=user.id,
        resource_label=user.name,
        request=request,
    )
    await session.commit()
    await session.refresh(user)
    return serialize_profile(user)


@router.get("/me/notification-settings", summary="Cài đặt thông báo email")
async def read_notification_settings(user: CurrentUser) -> dict[str, Any]:
    return serialize_settings(user)


@router.patch("/me/notification-settings", summary="Cập nhật cài đặt thông báo")
async def update_notification_settings(
    body: NotificationSettingsPatch, user: CurrentUser, session: SessionDep
) -> dict[str, Any]:
    prefs = user.notification_settings
    if prefs is None:
        prefs = UserNotificationSettings(user_id=user.id)
        session.add(prefs)
        await session.flush()
        user.notification_settings = prefs

    for wire, column in (
        ("emailOnResearchDone", "email_on_research_done"),
        ("emailOnApprovalRequest", "email_on_approval_request"),
        ("emailOnComment", "email_on_comment"),
        ("emailOnShare", "email_on_share"),
    ):
        value = getattr(body, wire)
        if value is not None:
            setattr(prefs, column, value)

    await session.commit()
    return serialize_settings(user)
