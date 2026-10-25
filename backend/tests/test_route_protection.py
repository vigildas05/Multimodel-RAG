"""Guards against a new endpoint shipping without authentication.

Static check: every route outside the public allow-list must have get_current_user in its
dependency tree. Behavioural check: every such route answers an anonymous request with 401.
"""

import uuid

import pytest
from fastapi.routing import APIRoute

from app.auth.dependencies import get_current_user, require_admin
from app.main import create_app
from app.routers.auth import PUBLIC_ROUTES
from helpers import fetch_csrf

ALWAYS_PUBLIC = PUBLIC_ROUTES | {("GET", "/health")}


def _dependency_calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _dependency_calls(dep)


def _api_routes():
    for route in create_app().routes:
        if isinstance(route, APIRoute):
            for method in route.methods:
                yield method, route.path, route


PROTECTED = [(m, p, r) for m, p, r in _api_routes() if (m, p) not in ALWAYS_PUBLIC]


def test_public_allow_list_is_small():
    # csrf, register, login, refresh, logout, demo, health. Growing this needs a real reason.
    assert len(ALWAYS_PUBLIC) <= 7


@pytest.mark.parametrize("method,path,route", PROTECTED, ids=[f"{m} {p}" for m, p, _ in PROTECTED])
def test_route_depends_on_authentication(method, path, route):
    assert get_current_user in set(_dependency_calls(route.dependant)), f"{method} {path} has no auth dependency"


def test_admin_routes_depend_on_admin_role():
    admin_routes = [(m, p, r) for m, p, r in PROTECTED if p.startswith("/admin")]
    assert admin_routes, "expected at least one admin route"
    for method, path, route in admin_routes:
        assert require_admin in set(_dependency_calls(route.dependant)), f"{method} {path} is not admin-only"


def test_anonymous_requests_get_401(client):
    # A valid CSRF token, so it's authentication (not CSRF) that rejects the request.
    fetch_csrf(client)
    for method, path, _ in PROTECTED:
        concrete = path.replace("{document_id}", str(uuid.uuid4()))
        resp = client.request(method, concrete)
        assert resp.status_code == 401, f"{method} {concrete} returned {resp.status_code}"
