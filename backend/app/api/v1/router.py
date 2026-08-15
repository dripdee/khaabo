"""v1 router assembly."""

from fastapi import APIRouter

from app.api.v1 import admin, discovery, dishes, restaurants, reviews, users
from app.api.v1.metrics import router as metrics_router

api_router = APIRouter()

api_router.include_router(discovery.router)
api_router.include_router(dishes.router)
api_router.include_router(restaurants.router)
api_router.include_router(reviews.router)
api_router.include_router(reviews.moderation_router)
api_router.include_router(users.router)
api_router.include_router(users.bookmarks_router)
api_router.include_router(admin.router)
api_router.include_router(metrics_router)

__all__ = ["api_router"]
