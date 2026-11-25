from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, verify_origin
from app.core.time import utcnow
from app.models.notification import Notification
from app.models.notification_preference import NotificationPreference
from app.models.user import User
from app.schemas.notification import NotificationOut, UnreadCountOut
from app.schemas.notification_preference import (
    NotificationPreferenceOut,
    NotificationPreferenceUpdate,
)
from app.services.domain_service import get_or_create_notification_preference

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=list[NotificationOut])
def list_notifications(
    unread_only: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Notification]:
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    stmt = stmt.order_by(Notification.created_at.desc()).limit(limit)
    return list(db.scalars(stmt).all())


@router.get("/unread-count", response_model=UnreadCountOut)
def unread_count(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> UnreadCountOut:
    n = db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
    )
    return UnreadCountOut(unread=int(n or 0))


@router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_read(
    notification_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_origin),
) -> Notification:
    n = db.get(Notification, notification_id)
    if n is None or n.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if n.read_at is None:
        n.read_at = utcnow()
    db.commit()
    db.refresh(n)
    return n


@router.post("/read-all", response_model=UnreadCountOut)
def mark_all_read(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_origin),
) -> UnreadCountOut:
    db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=utcnow())
    )
    db.commit()
    return UnreadCountOut(unread=0)


@router.get("/preferences", response_model=NotificationPreferenceOut)
def get_preferences(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> NotificationPreference:
    pref = get_or_create_notification_preference(db, user)
    db.commit()
    return pref


@router.put("/preferences", response_model=NotificationPreferenceOut)
def update_preferences(
    payload: NotificationPreferenceUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _: None = Depends(verify_origin),
) -> NotificationPreference:
    pref = get_or_create_notification_preference(db, user)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(pref, field, value)
    db.commit()
    db.refresh(pref)
    return pref
