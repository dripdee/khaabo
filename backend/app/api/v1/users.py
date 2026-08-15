"""User, profile, bookmark and collection endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, PaginationDep, rate_limit_write
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.models import (
    Bookmark,
    BookmarkCollection,
    City,
    Dish,
    Profile,
    Restaurant,
)
from app.models.enums import BookmarkTarget
from app.schemas.review import (
    BadgeOut,
    BookmarkCreate,
    BookmarkOut,
    CollectionCreate,
    CollectionOut,
    MeOut,
    ProfileOut,
    ProfileUpdate,
)
from app.services.gamification import list_badges
from app.utils.text import slugify

router = APIRouter(tags=["users"])


async def _profile_out(session: DbSession, profile: Profile) -> ProfileOut:
    city_slug = None
    if profile.city_id:
        city = await session.get(City, profile.city_id)
        city_slug = city.slug if city else None

    badges = await list_badges(session, profile.user_id)

    favourite_dishes: list[dict] = []
    if profile.favourite_dish_ids:
        rows = (
            await session.execute(
                select(Dish.id, Dish.name, Dish.slug).where(Dish.id.in_(profile.favourite_dish_ids))
            )
        ).all()
        favourite_dishes = [{"id": str(r.id), "name": r.name, "slug": r.slug} for r in rows]

    favourite_restaurants: list[dict] = []
    if profile.favourite_restaurant_ids:
        rows = (
            await session.execute(
                select(Restaurant.id, Restaurant.name, Restaurant.slug).where(
                    Restaurant.id.in_(profile.favourite_restaurant_ids)
                )
            )
        ).all()
        favourite_restaurants = [{"id": str(r.id), "name": r.name, "slug": r.slug} for r in rows]

    return ProfileOut(
        username=profile.username,
        display_name=profile.display_name,
        avatar_url=profile.avatar_url,
        bio=profile.bio,
        city_slug=city_slug,
        review_count=int(profile.review_count or 0),
        published_review_count=int(profile.published_review_count or 0),
        like_received_count=int(profile.like_received_count or 0),
        contribution_score=int(profile.contribution_score or 0),
        badges=[BadgeOut(**badge) for badge in badges],
        favourite_dishes=favourite_dishes,
        favourite_restaurants=favourite_restaurants,
        created_at=profile.created_at,
    )


@router.get("/users/me", response_model=MeOut, summary="Current user")
async def me(session: DbSession, user: CurrentUser) -> MeOut:
    """Also provisions the local user row on first call after Supabase signup."""
    profile = (
        await session.execute(select(Profile).where(Profile.user_id == user.id))
    ).scalar_one_or_none()

    return MeOut(
        id=str(user.id),
        email=user.email,
        role=user.role.value,
        profile=await _profile_out(session, profile) if profile else None,
    )


@router.patch("/users/me", response_model=MeOut, summary="Update own profile")
async def update_me(
    payload: ProfileUpdate,
    session: DbSession,
    user: CurrentUser,
    _: Annotated[None, Depends(rate_limit_write)] = None,
) -> MeOut:
    profile = (
        await session.execute(select(Profile).where(Profile.user_id == user.id))
    ).scalar_one_or_none()
    if profile is None:
        raise NotFoundError("Profile not found")

    if payload.username and payload.username != profile.username:
        taken = (
            await session.execute(
                select(Profile.user_id).where(Profile.username == payload.username)
            )
        ).first()
        if taken:
            raise ValidationError("That username is already taken")
        profile.username = payload.username

    for field in ("display_name", "bio", "avatar_url"):
        value = getattr(payload, field)
        if value is not None:
            setattr(profile, field, value)

    if payload.city_slug:
        city = (
            await session.execute(select(City).where(City.slug == payload.city_slug))
        ).scalar_one_or_none()
        if city is None:
            raise ValidationError(f"Unknown city '{payload.city_slug}'")
        profile.city_id = city.id

    if payload.favourite_dish_ids is not None:
        profile.favourite_dish_ids = [uuid.UUID(i) for i in payload.favourite_dish_ids]
    if payload.favourite_restaurant_ids is not None:
        profile.favourite_restaurant_ids = [uuid.UUID(i) for i in payload.favourite_restaurant_ids]

    return MeOut(
        id=str(user.id),
        email=user.email,
        role=user.role.value,
        profile=await _profile_out(session, profile),
    )


@router.get("/users/{username}", response_model=ProfileOut, summary="Public profile")
async def public_profile(username: str, session: DbSession) -> ProfileOut:
    profile = (
        await session.execute(select(Profile).where(Profile.username == username))
    ).scalar_one_or_none()
    if profile is None:
        raise NotFoundError("Unknown user")
    return await _profile_out(session, profile)


bookmarks_router = APIRouter(tags=["bookmarks"])


@bookmarks_router.post("/bookmarks", response_model=BookmarkOut, summary="Save an item")
async def create_bookmark(
    payload: BookmarkCreate,
    session: DbSession,
    user: CurrentUser,
    _: Annotated[None, Depends(rate_limit_write)] = None,
) -> BookmarkOut:
    """Idempotent: re-saving returns the existing bookmark instead of erroring."""
    dish_id = uuid.UUID(payload.dish_id) if payload.dish_id else None
    restaurant_id = uuid.UUID(payload.restaurant_id) if payload.restaurant_id else None

    # Mirror the DB CHECK constraint so the client gets a clear 422, not a 500.
    if payload.target_type is BookmarkTarget.DISH and (dish_id is None or restaurant_id):
        raise ValidationError("A dish bookmark requires dish_id only")
    if payload.target_type is BookmarkTarget.RESTAURANT and (restaurant_id is None or dish_id):
        raise ValidationError("A restaurant bookmark requires restaurant_id only")
    if payload.target_type is BookmarkTarget.DISH_RESTAURANT and not (dish_id and restaurant_id):
        raise ValidationError("A dish_restaurant bookmark requires both ids")

    existing = (
        await session.execute(
            select(Bookmark).where(
                Bookmark.user_id == user.id,
                Bookmark.target_type == payload.target_type,
                Bookmark.dish_id.is_(dish_id) if dish_id is None else Bookmark.dish_id == dish_id,
                Bookmark.restaurant_id.is_(restaurant_id)
                if restaurant_id is None
                else Bookmark.restaurant_id == restaurant_id,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = Bookmark(
            user_id=user.id,
            target_type=payload.target_type,
            dish_id=dish_id,
            restaurant_id=restaurant_id,
            collection_id=uuid.UUID(payload.collection_id) if payload.collection_id else None,
            note=payload.note,
        )
        session.add(existing)
        await session.flush()

    return await _bookmark_out(session, existing)


@bookmarks_router.delete("/bookmarks/{bookmark_id}", response_model=dict, summary="Remove")
async def delete_bookmark(bookmark_id: str, session: DbSession, user: CurrentUser) -> dict:
    bookmark = await session.get(Bookmark, uuid.UUID(bookmark_id))
    if bookmark is None:
        raise NotFoundError("Unknown bookmark")
    if bookmark.user_id != user.id:
        raise ForbiddenError("You can only remove your own bookmarks")
    await session.delete(bookmark)
    return {"status": "deleted", "id": bookmark_id}


@bookmarks_router.get("/bookmarks", response_model=dict, summary="List own bookmarks")
async def list_bookmarks(
    session: DbSession,
    user: CurrentUser,
    pagination: PaginationDep,
    collection_id: Annotated[str | None, Query()] = None,
    target_type: Annotated[str | None, Query()] = None,
) -> dict:
    stmt = select(Bookmark).where(Bookmark.user_id == user.id)
    if collection_id:
        stmt = stmt.where(Bookmark.collection_id == uuid.UUID(collection_id))
    if target_type:
        stmt = stmt.where(Bookmark.target_type == BookmarkTarget(target_type))

    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()

    rows = (
        (
            await session.execute(
                stmt.order_by(Bookmark.created_at.desc())
                .offset(pagination.offset)
                .limit(pagination.page_size)
            )
        )
        .scalars()
        .all()
    )

    items = [await _bookmark_out(session, row) for row in rows]

    return {
        "items": [item.model_dump(mode="json") for item in items],
        "page": pagination.page,
        "page_size": pagination.page_size,
        "total": int(total),
        "has_more": pagination.page * pagination.page_size < int(total),
    }


async def _bookmark_out(session: DbSession, bookmark: Bookmark) -> BookmarkOut:
    dish_name = dish_slug = restaurant_name = None
    if bookmark.dish_id:
        dish = await session.get(Dish, bookmark.dish_id)
        if dish:
            dish_name, dish_slug = dish.name, dish.slug
    if bookmark.restaurant_id:
        restaurant = await session.get(Restaurant, bookmark.restaurant_id)
        if restaurant:
            restaurant_name = restaurant.name

    return BookmarkOut(
        id=str(bookmark.id),
        target_type=bookmark.target_type.value,
        dish_id=str(bookmark.dish_id) if bookmark.dish_id else None,
        restaurant_id=str(bookmark.restaurant_id) if bookmark.restaurant_id else None,
        dish_name=dish_name,
        dish_slug=dish_slug,
        restaurant_name=restaurant_name,
        collection_id=str(bookmark.collection_id) if bookmark.collection_id else None,
        note=bookmark.note,
        created_at=bookmark.created_at,
    )


@bookmarks_router.post("/collections", response_model=CollectionOut, summary="New collection")
async def create_collection(
    payload: CollectionCreate,
    session: DbSession,
    user: CurrentUser,
    _: Annotated[None, Depends(rate_limit_write)] = None,
) -> CollectionOut:
    slug = slugify(payload.name)
    existing = (
        await session.execute(
            select(BookmarkCollection).where(
                BookmarkCollection.user_id == user.id, BookmarkCollection.slug == slug
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ValidationError("You already have a collection with that name")

    collection = BookmarkCollection(
        user_id=user.id,
        name=payload.name,
        slug=slug,
        description=payload.description,
        is_public=payload.is_public,
    )
    session.add(collection)
    await session.flush()

    return CollectionOut(
        id=str(collection.id),
        name=collection.name,
        slug=collection.slug,
        description=collection.description,
        is_public=collection.is_public,
        bookmark_count=0,
    )


@bookmarks_router.get("/collections", response_model=dict, summary="List collections")
async def list_collections(session: DbSession, user: CurrentUser) -> dict:
    rows = (
        await session.execute(
            select(
                BookmarkCollection,
                func.count(Bookmark.id).label("bookmark_count"),
            )
            .outerjoin(Bookmark, Bookmark.collection_id == BookmarkCollection.id)
            .where(BookmarkCollection.user_id == user.id)
            .group_by(BookmarkCollection.id)
            .order_by(BookmarkCollection.created_at.desc())
        )
    ).all()

    return {
        "items": [
            CollectionOut(
                id=str(collection.id),
                name=collection.name,
                slug=collection.slug,
                description=collection.description,
                is_public=collection.is_public,
                bookmark_count=int(count or 0),
            ).model_dump(mode="json")
            for collection, count in rows
        ]
    }
