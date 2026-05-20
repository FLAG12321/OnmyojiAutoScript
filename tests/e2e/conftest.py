import pytest


def pytest_addoption(parser):
    parser.addoption("--record", action="store_true", default=False, help="录制模式")
    parser.addoption("--replay", action="store_true", default=False, help="回放模式")


@pytest.fixture
def is_record(request):
    return request.config.getoption("--record")


@pytest.fixture
def is_replay(request):
    return request.config.getoption("--replay")
