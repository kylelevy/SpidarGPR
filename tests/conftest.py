"""
conftest.py
===========
Shared pytest fixtures for the spidar-gpr test suite.

All fixtures import from the installed package (spidar_gpr.*) so tests
always exercise the same code that end-users would install.
"""

import pytest
from spidar_gpr.mock_nic500 import MockNIC500


@pytest.fixture()
def mock():
    """Default mock: SDK mode, instant traces, no artificial drop."""
    with MockNIC500(trace_delay_s=0.0) as m:
        yield m


@pytest.fixture()
def non_sdk_mock():
    """Mock that advertises STANDARD mode — should trigger NICModeError."""
    with MockNIC500(sdk_mode=False) as m:
        yield m


@pytest.fixture()
def slow_mock():
    """Mock that emits one trace every 20 ms — useful for timing tests."""
    with MockNIC500(trace_delay_s=0.02) as m:
        yield m


@pytest.fixture()
def drop_mock():
    """Mock that silently closes the TCP socket after 5 traces."""
    with MockNIC500(drop_after=5, trace_delay_s=0.0) as m:
        yield m
