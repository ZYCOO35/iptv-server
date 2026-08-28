import pytest

from app.services.proxy_service import close_session


@pytest.fixture(autouse=True)
async def close_proxy_session_after_test():
    yield
    await close_session()
