"""Routers are mounted where their routes say they are."""

from fastapi.routing import APIRoute
from septum_api.main import _all_routers, app


def test_every_router_route_is_served_at_its_own_path():
    served = set(app.openapi()["paths"])
    for router in _all_routers:
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            assert route.path_format == route.path
            if route.include_in_schema:
                assert route.path in served, route.path
