"""API contract tests.

Anything requiring persistence is marked `db` and skipped without PostGIS. The
unmarked tests still verify the parts of the contract that matter most and need no
database: the OpenAPI surface, the error envelope, auth enforcement, validation
rules, and CORS.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


class TestOpenAPIContract:
    async def test_openapi_document_is_served(self, client: AsyncClient):
        response = await client.get("/api/v1/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert schema["info"]["version"] == "0.1.0"

    async def test_all_brief_endpoints_exist(self, client: AsyncClient):
        paths = (await client.get("/api/v1/openapi.json")).json()["paths"]
        required = [
            "/api/v1/search",
            "/api/v1/dishes/{slug}",
            "/api/v1/dishes/{slug}/restaurants",
            "/api/v1/dishes/{slug}/map",
            "/api/v1/restaurants/{restaurant_id}",
            "/api/v1/restaurants/{restaurant_id}/food-dna",
            "/api/v1/restaurants/{restaurant_id}/dishes",
            "/api/v1/restaurants/{restaurant_id}/reviews",
            "/api/v1/reviews",
            "/api/v1/likes",
            "/api/v1/bookmarks",
            "/api/v1/users/{username}",
            "/api/v1/trending",
        ]
        missing = [path for path in required if path not in paths]
        assert not missing, f"Missing endpoints from the brief: {missing}"

    async def test_root_advertises_attribution(self, client: AsyncClient):
        payload = (await client.get("/")).json()
        assert "© OpenStreetMap contributors" in payload["attribution"]

    async def test_request_id_is_echoed(self, client: AsyncClient):
        response = await client.get("/", headers={"x-request-id": "abc123"})
        assert response.headers["x-request-id"] == "abc123"


class TestAuthEnforcement:
    async def test_review_submission_requires_auth(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/reviews",
            json={"restaurant_id": "00000000-0000-0000-0000-000000000000", "body": "x" * 30},
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"

    async def test_likes_require_auth(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/likes", json={"review_id": "00000000-0000-0000-0000-000000000000"}
        )
        assert response.status_code == 401

    async def test_bookmarks_require_auth(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/bookmarks", json={"target_type": "dish", "dish_id": "x"}
        )
        assert response.status_code == 401

    async def test_moderation_queue_requires_auth(self, client: AsyncClient):
        assert (await client.get("/api/v1/moderation/queue")).status_code == 401

    async def test_malformed_bearer_is_rejected(self, client: AsyncClient):
        response = await client.get("/api/v1/users/me", headers={"Authorization": "NotBearer abc"})
        assert response.status_code == 401


class TestValidation:
    """Request-shape rules that need no database.

    Endpoints behind auth resolve their user dependency (and therefore touch the
    database) before body validation runs, so those cases live in the `db` class
    below instead.
    """

    async def test_search_rejects_lat_without_lng(self, client: AsyncClient):
        response = await client.get("/api/v1/search", params={"q": "momo", "lat": 22.5})
        assert response.status_code == 422

    async def test_search_rejects_inverted_price_range(self, client: AsyncClient):
        response = await client.get(
            "/api/v1/search", params={"q": "momo", "min_price": 500, "max_price": 100}
        )
        assert response.status_code == 422

    async def test_distance_sort_requires_coordinates(self, client: AsyncClient):
        response = await client.get("/api/v1/search", params={"q": "momo", "sort": "distance"})
        assert response.status_code == 422

    async def test_page_size_is_capped(self, client: AsyncClient):
        response = await client.get("/api/v1/search", params={"page_size": 9999})
        assert response.status_code == 422

    async def test_invalid_sort_option_is_rejected(self, client: AsyncClient):
        response = await client.get(
            "/api/v1/dishes/chicken-momo/restaurants", params={"sort": "magic"}
        )
        assert response.status_code == 422


class TestErrorEnvelope:
    async def test_errors_use_the_documented_shape(self, client: AsyncClient):
        payload = (await client.post("/api/v1/likes", json={"review_id": "x"})).json()
        assert set(payload.keys()) == {"error"}
        assert {"code", "message"} <= set(payload["error"].keys())

    async def test_unknown_route_returns_the_envelope(self, client: AsyncClient):
        payload = (await client.get("/api/v1/does-not-exist")).json()
        assert "error" in payload


class TestCors:
    async def test_allowed_origin_is_reflected(self, client: AsyncClient):
        response = await client.options(
            "/api/v1/search",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"

    async def test_unlisted_origin_is_not_allowed(self, client: AsyncClient):
        response = await client.options(
            "/api/v1/search",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.headers.get("access-control-allow-origin") != "https://evil.example.com"


@pytest.mark.db
class TestDatabaseBackedEndpoints:
    """Full-stack checks. Skipped automatically without PostGIS."""

    async def test_health_reports_components(self, client: AsyncClient):
        payload = (await client.get("/api/v1/health")).json()
        assert payload["status"] in {"ok", "degraded"}
        names = {component["name"] for component in payload["components"]}
        assert {"database", "redis", "ai_provider"} <= names

    async def test_cities_endpoint_lists_seeded_city(self, client: AsyncClient, session):
        payload = (await client.get("/api/v1/cities")).json()
        assert isinstance(payload, list)

    async def test_unknown_dish_returns_404_envelope(self, client: AsyncClient, session):
        response = await client.get("/api/v1/dishes/not-a-real-dish")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    async def test_unknown_city_returns_404(self, client: AsyncClient, session):
        response = await client.get("/api/v1/dishes/chicken-momo", params={"city": "atlantis"})
        assert response.status_code == 404

    async def test_me_provisions_a_profile(self, client: AsyncClient, session, auth_headers):
        payload = (await client.get("/api/v1/users/me", headers=auth_headers)).json()
        assert payload["role"] == "user"
        assert payload["profile"]["username"]

    async def test_admin_route_forbidden_for_plain_user(
        self, client: AsyncClient, session, auth_headers
    ):
        response = await client.get("/api/v1/admin/ranking", headers=auth_headers)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "forbidden"

    async def test_admin_route_allowed_for_admin(self, client: AsyncClient, session, admin_headers):
        response = await client.get("/api/v1/admin/ranking", headers=admin_headers)
        assert response.status_code == 200
        body = response.json()
        assert abs(sum(body["weights"].values()) - 1.0) < 1e-3

    async def test_short_review_body_is_rejected(self, client: AsyncClient, session, auth_headers):
        response = await client.post(
            "/api/v1/reviews",
            json={"restaurant_id": "00000000-0000-0000-0000-000000000000", "body": "great"},
            headers=auth_headers,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    async def test_unknown_field_is_rejected(self, client: AsyncClient, session, auth_headers):
        response = await client.post(
            "/api/v1/reviews",
            json={
                "restaurant_id": "00000000-0000-0000-0000-000000000000",
                "body": "The chicken momo was excellent and juicy today",
                "unexpected_field": "boom",
            },
            headers=auth_headers,
        )
        assert response.status_code == 422

    async def test_rating_out_of_range_is_rejected(
        self, client: AsyncClient, session, auth_headers
    ):
        response = await client.post(
            "/api/v1/reviews",
            json={
                "restaurant_id": "00000000-0000-0000-0000-000000000000",
                "body": "The chicken momo was excellent and juicy today",
                "rating": 99,
            },
            headers=auth_headers,
        )
        assert response.status_code == 422
