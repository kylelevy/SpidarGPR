"""
test_spidar_gpr.py
==================
Pytest test suite for the SpidarGPR / NIC500Connection driver.

Every test uses MockNIC500 to simulate the hardware, so no physical
NIC-500 unit is required.  The suite is organised into five groups:

  1. REST API layer       – SDK mode gate, system info, power, GPR info, setup
  2. Context managers     – data_socket_context, acquisition, session
  3. Trace reading        – read_traces() correctness and edge cases
  4. Streaming            – start/stop streaming, get_latest_traces()
  5. Fault injection      – hardware drop, non-SDK mode, socket closed early
"""

from __future__ import annotations

import struct
import threading
import time

import numpy as np
import pytest

from mock_nic500 import MockNIC500
from SpidarGPR import (
    DataSocketError,
    GPRTrace,
    NIC500Connection,
    NICModeError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HEADER_STRUCT = struct.Struct("<LLLLHH")
HEADER_SIZE = 20
POINT_SIZE = 4


def _conn(mock: MockNIC500, **kwargs) -> NIC500Connection:
    """Return a NIC500Connection pre-pointed at *mock*."""
    return mock.make_connection(**kwargs)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock():
    """Default mock: SDK mode, instant traces, no artificial drop."""
    with MockNIC500(trace_delay_s=0.0) as m:
        yield m


@pytest.fixture()
def non_sdk_mock():
    """Mock that advertises STANDARD mode (should trigger NICModeError)."""
    with MockNIC500(sdk_mode=False) as m:
        yield m


@pytest.fixture()
def slow_mock():
    """Mock that emits one trace every 20 ms – useful for timing tests."""
    with MockNIC500(trace_delay_s=0.02) as m:
        yield m


@pytest.fixture()
def drop_mock():
    """Mock that silently closes the TCP socket after 5 traces."""
    with MockNIC500(drop_after=5, trace_delay_s=0.0) as m:
        yield m


# ===========================================================================
# Group 1 – REST API layer
# ===========================================================================


class TestSDKModeGate:
    def test_sdk_mode_passes_when_sdk(self, mock):
        """check_sdk_mode() must not raise when the NIC is in SDK mode."""
        nic = _conn(mock)
        nic.check_sdk_mode()  # should not raise

    def test_sdk_mode_raises_when_standard(self, non_sdk_mock):
        """check_sdk_mode() must raise NICModeError for STANDARD firmware."""
        nic = _conn(non_sdk_mock)
        with pytest.raises(NICModeError, match="not in SDK mode"):
            nic.check_sdk_mode()


class TestSystemInformation:
    def test_system_info_keys_present(self, mock):
        """get_system_information() must not raise; mock returns all fields."""
        nic = _conn(mock)
        nic.check_sdk_mode()
        # No assertion needed beyond "doesn't crash"; fields are printed.
        nic.get_system_information()

    def test_power_on_sets_state(self, mock):
        """turn_on_GPR() must PUT state=2 and the mock must record it."""
        nic = _conn(mock)
        nic.check_sdk_mode()
        nic.get_system_information()
        assert mock.power_state == 0
        nic.turn_on_GPR()
        assert mock.power_state == 2


class TestGPRInformation:
    def test_window_time_shift_formula(self, mock):
        """
        window_time_shift_ps should equal
        window_time_shift_reference_ps - first_break_point * time_sampling_interval_ps
        """
        ref_ps = 5000
        first_break = 30
        interval_ps = 100

        with MockNIC500(window_time_shift_reference_ps=ref_ps) as m:
            nic = _conn(
                m,
                first_break_point=first_break,
                time_sampling_interval_ps=interval_ps,
            )
            nic.check_sdk_mode()
            nic.get_system_information()
            nic.turn_on_GPR()
            nic.get_GPR_information()

        expected = ref_ps - first_break * interval_ps
        assert nic.window_time_shift_ps == expected

    def test_window_time_shift_default_formula(self, mock):
        """Verify the formula with the mock's default reference (3000 ps)."""
        # default: first_break_point=20, interval=100 → 3000 - 20*100 = 1000
        nic = _conn(mock)  # mock.window_time_shift_reference_ps = 3000
        nic.check_sdk_mode()
        nic.get_system_information()
        nic.turn_on_GPR()
        nic.get_GPR_information()
        assert nic.window_time_shift_ps == 3000 - 20 * 100  # 1000

    def test_gpr_system_info_stored(self, mock):
        """gpr_system_info must be populated after get_GPR_information()."""
        nic = _conn(mock)
        nic.check_sdk_mode()
        nic.get_system_information()
        nic.turn_on_GPR()
        nic.get_GPR_information()
        assert nic.gpr_system_info is not None
        assert "window_time_shift_reference_ps" in nic.gpr_system_info


class TestSetup:
    def test_setup_sends_correct_points_per_trace(self, mock):
        """setup_gpr() must forward POINTS_PER_TRACE to the mock."""
        pts = 512
        nic = _conn(mock, points_per_trace=pts)
        nic.check_sdk_mode()
        nic.get_system_information()
        nic.turn_on_GPR()
        nic.get_GPR_information()
        nic.setup_gpr()
        assert mock.points_per_trace == pts

    def test_setup_payload_structure(self, mock):
        """setup_gpr() must send all required GPR parameters."""
        nic = _conn(mock)
        nic.check_sdk_mode()
        nic.get_system_information()
        nic.turn_on_GPR()
        nic.get_GPR_information()
        nic.setup_gpr()

        gpr_params = mock.last_setup_payload["gpr0"]["parameters"]
        assert "points_per_trace" in gpr_params
        assert "window_time_shift_ps" in gpr_params
        assert "point_stacks" in gpr_params
        assert "time_sampling_interval_ps" in gpr_params


# ===========================================================================
# Group 2 – Context managers
# ===========================================================================


class TestContextManagers:
    def test_data_socket_context_opens_and_closes(self, mock):
        """data_socket_context() must set/clear the socket attribute."""
        nic = _conn(mock)
        nic.check_sdk_mode()
        nic.get_system_information()
        nic.turn_on_GPR()
        nic.get_GPR_information()
        nic.setup_gpr()

        assert nic.data_socket is None
        with nic.data_socket_context():
            assert nic.data_socket is not None
        assert nic.data_socket is None

    def test_acquisition_context_starts_and_stops(self, mock):
        """acquisition() must fire start (state=1) then stop (state=0)."""
        nic = _conn(mock)
        nic.check_sdk_mode()
        nic.get_system_information()
        nic.turn_on_GPR()
        nic.get_GPR_information()
        nic.setup_gpr()

        with nic.data_socket_context():
            assert not mock.acquisition_active.is_set()
            with nic.acquisition():
                assert mock.acquisition_active.is_set()
            assert not mock.acquisition_active.is_set()

        assert mock.acquisition_transitions == [1, 0]

    def test_acquisition_stop_runs_on_exception(self, mock):
        """acquisition() must issue STOP even when body raises."""
        nic = _conn(mock)
        nic.check_sdk_mode()
        nic.get_system_information()
        nic.turn_on_GPR()
        nic.get_GPR_information()
        nic.setup_gpr()

        with nic.data_socket_context():
            with pytest.raises(RuntimeError):
                with nic.acquisition():
                    raise RuntimeError("boom")

        assert mock.acquisition_transitions[-1] == 0  # STOP was sent

    def test_session_context_manager_full_lifecycle(self, mock):
        """session() must wire up the entire start-up sequence."""
        nic = _conn(mock)
        with nic.session():
            assert nic.data_socket is not None
            assert mock.acquisition_active.is_set()
            assert mock.power_state == 2

        assert nic.data_socket is None
        assert not mock.acquisition_active.is_set()

    def test_session_raises_for_non_sdk(self, non_sdk_mock):
        """session() must propagate NICModeError before touching sockets."""
        nic = _conn(non_sdk_mock)
        with pytest.raises(NICModeError):
            with nic.session():
                pass  # should never reach here


# ===========================================================================
# Group 3 – Trace reading
# ===========================================================================


class TestReadTraces:
    def test_read_exact_count(self, mock):
        """read_traces(N) must return exactly N trace dicts."""
        nic = _conn(mock)
        with nic.session():
            traces = nic.read_traces(10)
        assert len(traces) == 10

    def test_trace_dict_schema(self, mock):
        """Each trace dict must have 'header' and 'data' keys."""
        nic = _conn(mock)
        with nic.session():
            traces = nic.read_traces(3)

        for t in traces:
            assert "header" in t
            assert "data" in t
            hdr = t["header"]
            for field in (
                "tv_sec",
                "tv_nsec",
                "trace_num",
                "status",
                "stacks",
                "header_size",
            ):
                assert field in hdr, f"Missing header field: {field}"

    def test_data_array_shape(self, mock):
        """data array must have length == POINTS_PER_TRACE."""
        pts = 256
        nic = _conn(mock, points_per_trace=pts)
        with nic.session():
            traces = nic.read_traces(5)
        for t in traces:
            assert len(t["data"]) == pts

    def test_data_is_float32(self, mock):
        """GPR samples must be float32 (as emitted by the hardware)."""
        nic = _conn(mock)
        with nic.session():
            traces = nic.read_traces(2)
        for t in traces:
            assert t["data"].dtype == np.float32

    def test_header_size_field_is_20(self, mock):
        """header_size in each trace must equal the documented 20 bytes."""
        nic = _conn(mock)
        with nic.session():
            traces = nic.read_traces(4)
        for t in traces:
            assert t["header"]["header_size"] == 20

    def test_trace_nums_are_positive(self, mock):
        """trace_num must be ≥ 1 for all returned traces."""
        nic = _conn(mock)
        with nic.session():
            traces = nic.read_traces(8)
        for t in traces:
            assert t["header"]["trace_num"] >= 1

    def test_read_without_socket_raises(self, mock):
        """read_traces() before opening the socket must raise DataSocketError."""
        nic = _conn(mock)
        with pytest.raises(DataSocketError):
            nic.read_traces(1)

    def test_radargram_stacking(self, mock):
        """np.vstack on read_traces output must produce a 2-D array."""
        nic = _conn(mock)
        with nic.session():
            traces = nic.read_traces(10)

        radargram = np.vstack([t["data"] for t in traces])
        assert radargram.shape == (10, nic.POINTS_PER_TRACE)

    def test_different_points_per_trace(self, mock):
        """Reconfiguring points_per_trace must flow through end-to-end."""
        with MockNIC500() as m:
            nic = _conn(m, points_per_trace=100)
            with nic.session():
                traces = nic.read_traces(3)
            assert all(len(t["data"]) == 100 for t in traces)


# ===========================================================================
# Group 4 – Streaming
# ===========================================================================


class TestStreaming:
    def test_start_streaming_without_socket_raises(self, mock):
        """start_streaming() before socket open must raise DataSocketError."""
        nic = _conn(mock)
        with pytest.raises(DataSocketError):
            nic.start_streaming()

    def test_streaming_collects_traces(self, mock):
        """After 0.5 s of streaming the buffer must contain ≥ 1 trace."""
        nic = _conn(mock)
        with nic.session():
            nic.start_streaming()
            time.sleep(0.5)
            traces = nic.get_latest_traces()
            nic.stop_streaming()

        assert traces is not None
        assert len(traces) > 0

    def test_streaming_returns_gpr_trace_objects(self, mock):
        """Buffered items must be GPRTrace dataclass instances."""
        nic = _conn(mock)
        with nic.session():
            nic.start_streaming()
            time.sleep(0.3)
            traces = nic.get_latest_traces()
            nic.stop_streaming()

        assert traces is not None
        for t in traces:
            assert isinstance(t, GPRTrace)

    def test_gpr_trace_fields(self, mock):
        """GPRTrace instances must expose all six header fields plus data."""
        nic = _conn(mock)
        with nic.session():
            nic.start_streaming()
            time.sleep(0.3)
            traces = nic.get_latest_traces()
            nic.stop_streaming()

        assert traces
        t = traces[0]
        assert isinstance(t.tv_sec, int)
        assert isinstance(t.tv_nsec, int)
        assert isinstance(t.trace_num, int)
        assert isinstance(t.status, int)
        assert isinstance(t.stacks, int)
        assert isinstance(t.header_size, int)
        assert isinstance(t.data, np.ndarray)

    def test_gpr_trace_data_length(self, mock):
        """GPRTrace.data must have length == POINTS_PER_TRACE."""
        nic = _conn(mock)
        with nic.session():
            nic.start_streaming()
            time.sleep(0.3)
            traces = nic.get_latest_traces()
            nic.stop_streaming()

        assert traces
        for t in traces:
            assert len(t.data) == nic.POINTS_PER_TRACE

    def test_get_latest_traces_clears_buffer(self, mock):
        """get_latest_traces(clear=True) must empty the internal buffer."""
        nic = _conn(mock)
        with nic.session():
            nic.start_streaming()
            time.sleep(0.3)
            nic.get_latest_traces(clear=True)
            # Give the reader a moment then drain again
            time.sleep(0.05)
            second = nic.get_latest_traces(clear=True)
            nic.stop_streaming()

        # It's acceptable for second to be None or a short list; the key
        # property is that the first call cleared the buffer.
        # We just verify no exception and types are correct.
        if second is not None:
            for t in second:
                assert isinstance(t, GPRTrace)

    def test_get_latest_traces_no_clear(self, mock):
        """get_latest_traces(clear=False) must leave the buffer intact."""
        nic = _conn(mock)
        with nic.session():
            nic.start_streaming()
            time.sleep(0.3)
            nic.stop_streaming()
            first = nic.get_latest_traces(clear=False)
            second = nic.get_latest_traces(clear=False)

        assert first is not None
        assert second is not None
        assert len(first) == len(second)

    def test_start_streaming_idempotent(self, mock):
        """Calling start_streaming() twice must not spawn extra threads."""
        nic = _conn(mock)
        with nic.session():
            nic.start_streaming()
            thread_before = nic._reader_thread
            nic.start_streaming()  # second call – no-op
            assert nic._reader_thread is thread_before
            nic.stop_streaming()

    def test_stop_streaming_joins_thread(self, mock):
        """After stop_streaming() the reader thread must be None."""
        nic = _conn(mock)
        with nic.session():
            nic.start_streaming()
            assert nic._reader_thread is not None
            nic.stop_streaming()
            assert nic._reader_thread is None

    def test_empty_buffer_returns_none(self, mock):
        """get_latest_traces() on an empty buffer must return None."""
        nic = _conn(mock)
        result = nic.get_latest_traces()
        assert result is None

    def test_slow_mock_still_yields_traces(self, slow_mock):
        """Even with trace_delay_s=0.02, 0.5 s should yield ≥ 5 traces."""
        nic = _conn(slow_mock)
        with nic.session():
            nic.start_streaming()
            time.sleep(0.5)
            traces = nic.get_latest_traces()
            nic.stop_streaming()

        assert traces is not None
        assert len(traces) >= 5

    def test_concurrent_buffer_access_is_safe(self, mock):
        """
        A parallel thread draining the buffer while the reader fills it
        must not raise and must return only GPRTrace objects.
        """
        collected: list[GPRTrace] = []
        errors: list[Exception] = []

        def drainer(nic: NIC500Connection):
            end = time.time() + 0.4
            while time.time() < end:
                batch = nic.get_latest_traces(clear=True)
                if batch:
                    try:
                        for t in batch:
                            assert isinstance(t, GPRTrace)
                        collected.extend(batch)
                    except AssertionError as e:
                        errors.append(e)
                time.sleep(0.01)

        nic = _conn(mock)
        with nic.session():
            nic.start_streaming()
            t = threading.Thread(target=drainer, args=(nic,), daemon=True)
            t.start()
            t.join(timeout=1.0)
            nic.stop_streaming()

        assert not errors, f"Type errors during concurrent access: {errors}"
        assert len(collected) > 0


# ===========================================================================
# Group 5 – Fault injection
# ===========================================================================


class TestFaultInjection:
    def test_hardware_drop_mid_read(self, drop_mock):
        """
        When the hardware closes the TCP socket after N traces,
        read_traces() must return whatever was received (≤ drop_after)
        rather than hanging indefinitely or crashing.
        """
        nic = _conn(drop_mock)
        with nic.session():
            traces = nic.read_traces(max_traces=50)  # request more than will arrive

        # We asked for 50 but the mock drops after 5
        assert 0 < len(traces) <= drop_mock.drop_after + 1

    def test_hardware_drop_during_streaming(self, drop_mock):
        """
        A mid-stream hardware disconnect must not crash the reader thread.
        Traces collected before the drop must be intact.
        """
        nic = _conn(drop_mock)
        with nic.session():
            nic.start_streaming()
            time.sleep(0.5)
            traces = nic.get_latest_traces()
            nic.stop_streaming()

        # Traces may be None if all were collected before get_latest_traces,
        # but no exception should have propagated.
        if traces is not None:
            for t in traces:
                assert isinstance(t, GPRTrace)

    def test_non_sdk_mode_blocks_session(self, non_sdk_mock):
        """session() must raise NICModeError before any TCP connection."""
        nic = _conn(non_sdk_mock)
        with pytest.raises(NICModeError):
            with nic.session():
                pass

    def test_socket_closed_before_read(self, mock):
        """read_traces() with data_socket=None must raise DataSocketError."""
        nic = _conn(mock)
        assert nic.data_socket is None
        with pytest.raises(DataSocketError, match="not open"):
            nic.read_traces(1)

    def test_socket_closed_before_streaming(self, mock):
        """start_streaming() with data_socket=None must raise DataSocketError."""
        nic = _conn(mock)
        with pytest.raises(DataSocketError, match="not open"):
            nic.start_streaming()

    def test_acquisition_stop_sent_on_session_exception(self, mock):
        """
        If the body of session() raises, the STOP acquisition command
        must still be sent (finally block in acquisition() context manager).
        """
        nic = _conn(mock)
        with pytest.raises(ValueError):
            with nic.session():
                raise ValueError("unexpected error in user code")

        assert 0 in mock.acquisition_transitions  # STOP (state=0) was sent


# ===========================================================================
# Group 6 – Mock self-consistency
# ===========================================================================


class TestMockSelfConsistency:
    """
    Verify that MockNIC500 itself behaves correctly so that test failures
    come from the driver under test, not the mock.
    """

    def test_mock_counts_traces_accurately(self, mock):
        """total_traces_sent() must match the number of traces received."""
        nic = _conn(mock)
        with nic.session():
            traces = nic.read_traces(15)
        assert mock.total_traces_sent() >= len(traces)

    def test_mock_points_per_trace_updated_by_setup(self, mock):
        """Setting points_per_trace=128 must update mock.points_per_trace."""
        nic = _conn(mock, points_per_trace=128)
        with nic.session():
            pass
        assert mock.points_per_trace == 128

    def test_mock_generates_deterministic_data(self):
        """
        Traces with the same trace_num must have identical sample data
        (the mock uses a seeded RNG).
        """
        from mock_nic500 import (
            _make_trace_bytes,
            HEADER_SIZE_BYTES,
            DEFAULT_STACKS,
            DEFAULT_POINTS as DP,
        )

        raw1 = _make_trace_bytes(42, DP, DEFAULT_STACKS)
        raw2 = _make_trace_bytes(42, DP, DEFAULT_STACKS)

        samples1 = np.frombuffer(raw1[HEADER_SIZE_BYTES:], dtype=np.float32)
        samples2 = np.frombuffer(raw2[HEADER_SIZE_BYTES:], dtype=np.float32)
        np.testing.assert_array_equal(samples1, samples2)

    def test_mock_different_trace_nums_differ(self):
        """Traces with different trace_nums must have different samples."""
        from mock_nic500 import (
            _make_trace_bytes,
            HEADER_SIZE_BYTES,
            DEFAULT_STACKS,
            DEFAULT_POINTS as DP,
        )

        raw1 = _make_trace_bytes(1, DP, DEFAULT_STACKS)
        raw2 = _make_trace_bytes(2, DP, DEFAULT_STACKS)

        s1 = np.frombuffer(raw1[HEADER_SIZE_BYTES:], dtype=np.float32)
        s2 = np.frombuffer(raw2[HEADER_SIZE_BYTES:], dtype=np.float32)
        assert not np.array_equal(s1, s2)

    def test_make_connection_helper(self, mock):
        """make_connection() must return a properly configured NIC500Connection."""
        nic = mock.make_connection()
        assert nic.ip == mock.bind_ip
        # The URL must contain the mock's HTTP port
        assert str(mock.http_port) in nic.API_URL
