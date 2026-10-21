"""The bcrypt pool: dedicated, off the loop, recreated after shutdown."""

import threading

from septum_api.services import auth as auth_service


def _thread_name() -> str:
    return threading.current_thread().name


async def test_work_runs_on_the_dedicated_bcrypt_pool():
    auth_service.shutdown_password_executor()
    try:
        name = await auth_service.run_password_work(_thread_name)
        assert name.startswith("bcrypt")
        assert threading.current_thread().name != name
    finally:
        auth_service.shutdown_password_executor()


async def test_shutdown_then_use_creates_a_fresh_pool():
    auth_service.shutdown_password_executor()
    try:
        await auth_service.run_password_work(_thread_name)
        first = auth_service._password_executor
        assert first is not None

        auth_service.shutdown_password_executor()
        assert auth_service._password_executor is None

        assert (await auth_service.run_password_work(_thread_name)).startswith("bcrypt")
        assert auth_service._password_executor is not first
    finally:
        auth_service.shutdown_password_executor()


def test_shutdown_without_a_pool_is_a_noop():
    auth_service.shutdown_password_executor()
    auth_service.shutdown_password_executor()
    assert auth_service._password_executor is None
