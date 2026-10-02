"""Admin-only discovery API; credentials remain in server configuration."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .search import COUNTRIES
from .workflow import VersionConflict


class PlanInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    country: str
    industry: str = Field(min_length=1, max_length=100)
    product: str = Field(min_length=1, max_length=150)
    objective: str = Field(min_length=1, max_length=1000)
    provider: Literal["brave", "tavily"]
    queries: list[Annotated[str, Field(min_length=3, max_length=300)]] = Field(
        min_length=1, max_length=5
    )
    interval_hours: Literal[0, 6, 24, 168] = 24
    tier: Literal["1A", "1B", "1C", "1D", "2A", "2B"] = "1B"
    version: int | None = Field(default=None, ge=0)

    @field_validator("country")
    @classmethod
    def country_supported(cls, value):
        if value not in COUNTRIES:
            raise ValueError("目标国家暂不支持")
        return value

    @field_validator("name", "industry", "product", "objective")
    @classmethod
    def clean_text(cls, value):
        if not value.strip():
            raise ValueError("请填写计划目标")
        return value.strip()

    @field_validator("queries")
    @classmethod
    def clean_queries(cls, values):
        values = [" ".join(v.split()) for v in values]
        if any(len(v) < 3 for v in values):
            raise ValueError("搜索词至少 3 个字符")
        if len(set(v.casefold() for v in values)) != len(values):
            raise ValueError("请移除重复搜索词")
        return values


class ToggleInput(BaseModel):
    version: int = Field(ge=0)
    enabled: bool


class RunInput(BaseModel):
    version: int = Field(ge=0)
    request_id: UUID


class ReviewInput(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=30)
    action: Literal["collect", "reject"]
    reason: str = Field(default="", max_length=500)


def routes(store, services):
    router = APIRouter(prefix="/api/discovery")

    def admin(request):
        if request.state.role != "admin":
            raise HTTPException(403, "只有管理员可以管理发现计划")
        return request.state.identity

    def invoke(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except VersionConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("")
    def state(request: Request):
        admin(request)
        return store.discovery_state(services)

    @router.post("/plans", status_code=201)
    def create(body: PlanInput, request: Request):
        return invoke(store.save_plan, body.model_dump(exclude={"version"}), admin(request))

    @router.post("/plans/{plan_id}")
    def edit(plan_id: str, body: PlanInput, request: Request):
        return invoke(
            store.save_plan,
            body.model_dump(exclude={"version"}),
            admin(request),
            plan_id,
            body.version,
        )

    @router.post("/plans/{plan_id}/schedule")
    def schedule(plan_id: str, body: ToggleInput, request: Request):
        return invoke(
            store.toggle_plan, plan_id, body.enabled, body.version, admin(request), services
        )

    @router.post("/plans/{plan_id}/runs", status_code=201)
    def run(plan_id: str, body: RunInput, request: Request):
        return invoke(
            store.queue_search,
            plan_id,
            str(body.request_id),
            body.version,
            admin(request),
            services,
        )

    @router.get("/plans/{plan_id}/runs")
    def history(plan_id: str, request: Request, before: str | None = None):
        admin(request)
        return invoke(store.search_runs, plan_id, before)

    @router.get("/runs/{run_id}")
    def run_details(run_id: str, request: Request):
        admin(request)
        return invoke(store.search_run, run_id)

    @router.post("/review")
    def review(body: ReviewInput, request: Request):
        return invoke(store.review_results, body.ids, body.action, admin(request), body.reason)

    return router
