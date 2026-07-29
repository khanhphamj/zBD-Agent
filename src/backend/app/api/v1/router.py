from fastapi import APIRouter

from app.api.v1 import (
    admin,
    ai,
    auth,
    customers,
    dashboard,
    exports,
    health,
    jobs,
    knowledge,
    me,
    notifications,
    products,
    proposals,
    search,
    shares,
    skills,
    templates,
)

api_router = APIRouter()

api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(me.router)
api_router.include_router(dashboard.router)
api_router.include_router(customers.router)
api_router.include_router(jobs.router)
api_router.include_router(products.router)
api_router.include_router(proposals.router)
api_router.include_router(ai.router)
api_router.include_router(exports.router)
api_router.include_router(knowledge.router)
api_router.include_router(skills.router)
api_router.include_router(templates.router)
api_router.include_router(shares.router)
api_router.include_router(search.router)
api_router.include_router(notifications.router)
api_router.include_router(admin.router)

__all__ = ["api_router"]
