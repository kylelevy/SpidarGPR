"""
mock_nic500.py — In-process mock of the NIC-500 hardware unit.

Spins up two servers on ephemeral ports:
  - HTTP server reproducing every REST endpoint used by NIC500Connection.
  - TCP data-socket server that streams binary GPR traces.
"""

from __future__ import annotations
import json, socket, struct, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional
import numpy as np

_HEADER_STRUCT = struct.Struct("<LLLLHH")
HEADER_SIZE_BYTES = 20
DEFAULT_POINTS = 200
DEFAULT_STACKS = 4


def _ok(message="Success"):
    return {"status": {"status_code": 0, "message": message}}


def _make_trace_bytes(trace_num, points, stacks):
    now = time.time()
    header = _HEADER_STRUCT.pack(
        int(now), int((now % 1) * 1e9), trace_num, 0, stacks, HEADER_SIZE_BYTES
    )
    rng = np.random.default_rng(trace_num)
    data = (rng.standard_normal(points) * 100).astype(np.float32)
    return header + data.tobytes()


class _Handler(BaseHTTPRequestHandler):
    _mock: "MockNIC500"

    def log_message(self, *_):
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_form_json(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n).decode()
        qs = urllib.parse.parse_qs(raw)
        return json.loads(qs.get("data", ["{}"])[0])

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        m = self._mock
        if path == "/api":
            name = "NIC-500 SDK" if m.sdk_mode else "NIC-500 STANDARD"
            self._send_json({**_ok(), "data": {"name": name}})
        elif path == "/api/nic/system_information":
            self._send_json(
                {
                    **_ok(),
                    "data": {
                        "app_dip": 0,
                        "app_version": "1.0.0-mock",
                        "fpga_version": "2.3.0-mock",
                        "hardware_id": "MOCK-HW-001",
                        "kernel_version": "5.4.0-mock",
                        "nic_serial_number": "SN-MOCK-0001",
                        "os_version": "Ubuntu 20.04",
                    },
                }
            )
        elif path == "/api/nic/gpr/system_information":
            self._send_json(
                {
                    **_ok(),
                    "data": {
                        "gpr": {
                            "window_time_shift_reference_ps": m.window_time_shift_reference_ps,
                            "points_per_trace": m.points_per_trace,
                        }
                    },
                }
            )
        elif path == "/api/nic/gpr/data_socket":
            self._send_json({**_ok(), "data": {"data_socket": {"port": m.data_port}}})
        elif path == "/api/nic/version":
            self._send_json({**_ok(), "data": {"version": "1.0.0-mock"}})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_PUT(self):
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        m = self._mock
        payload = self._read_form_json()
        if path == "/api/nic/power":
            m.power_state = payload.get("state", 0)
            self._send_json(_ok())
        elif path == "/api/nic/setup":
            params = payload.get("gpr0", {}).get("parameters", {})
            if "points_per_trace" in params:
                m.points_per_trace = params["points_per_trace"]
            # Record the last setup payload for inspection in tests
            m.last_setup_payload = payload
            self._send_json(_ok())
        elif path == "/api/nic/acquisition":
            state = payload.get("state", 0)
            if state == 1:
                m.acquisition_active.set()
            else:
                m.acquisition_active.clear()
            # Track how many start/stop transitions have happened
            m.acquisition_transitions.append(state)
            self._send_json(_ok())
        else:
            self._send_json({"error": "not found"}, 404)


class MockNIC500:
    """
    Self-contained mock of the NIC-500 hardware unit.

    Parameters
    ----------
    sdk_mode
        False → /api returns "NIC-500 STANDARD" (triggers NICModeError).
    window_time_shift_reference_ps
        Value returned by GPR system-info endpoint.
    drop_after
        Close data socket after this many traces (simulate hardware drop).
    trace_delay_s
        Sleep between emitted traces (0 = as fast as possible).
    bind_ip
        IP address to bind both servers to (default "127.0.0.1").
    """

    def __init__(
        self,
        *,
        sdk_mode: bool = True,
        window_time_shift_reference_ps: int = 3000,
        drop_after: Optional[int] = None,
        trace_delay_s: float = 0.0,
        bind_ip: str = "127.0.0.1",
    ):
        self.sdk_mode = sdk_mode
        self.window_time_shift_reference_ps = window_time_shift_reference_ps
        self.drop_after = drop_after
        self.trace_delay_s = trace_delay_s
        self.bind_ip = bind_ip

        self.points_per_trace = DEFAULT_POINTS
        self.power_state = 0
        self.acquisition_active = threading.Event()
        self.acquisition_transitions: list[int] = []  # ← new: records state changes
        self.last_setup_payload: dict = {}  # ← new: last /setup payload

        self._stop = threading.Event()
        self._client_conn: Optional[socket.socket] = None
        self._trace_count = 0
        self._trace_count_lock = threading.Lock()

        self._srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv_sock.bind((bind_ip, 0))
        self._srv_sock.listen(5)
        self._data_port = self._srv_sock.getsockname()[1]

        mock = self

        class BoundHandler(_Handler):
            _mock = mock

        self._http_server = HTTPServer((bind_ip, 0), BoundHandler)
        self._http_port = self._http_server.server_address[1]
        self._http_thread = threading.Thread(
            target=self._http_server.serve_forever, daemon=True
        )
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)

    @property
    def http_port(self) -> int:
        return self._http_port

    @property
    def data_port(self) -> int:
        return self._data_port

    def start(self) -> "MockNIC500":
        self._http_thread.start()
        self._accept_thread.start()
        return self

    def stop(self):
        self._stop.set()
        self.acquisition_active.set()  # unblock any waiting _stream_to
        self._http_server.shutdown()
        try:
            self._srv_sock.close()
        except OSError:
            pass
        self._close_client()

    def __enter__(self) -> "MockNIC500":
        return self.start()

    def __exit__(self, *_):
        self.stop()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                self._srv_sock.settimeout(0.3)
                conn, _ = self._srv_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self._close_client()
            self._client_conn = conn
            self._stream_to(conn)
            self._close_client()

    def _stream_to(self, conn: socket.socket):
        local = 0
        while not self._stop.is_set():
            if not self.acquisition_active.wait(timeout=0.1):
                continue
            if self._stop.is_set():
                break
            if self.drop_after is not None and local >= self.drop_after:
                conn.close()
                return
            try:
                with self._trace_count_lock:
                    num = self._trace_count + 1
                    self._trace_count += 1
                conn.sendall(
                    _make_trace_bytes(num, self.points_per_trace, DEFAULT_STACKS)
                )
                local += 1
            except OSError:
                return
            if self.trace_delay_s:
                time.sleep(self.trace_delay_s)

    def _close_client(self):
        if self._client_conn:
            try:
                self._client_conn.close()
            except Exception:
                pass
            self._client_conn = None

    def total_traces_sent(self) -> int:
        with self._trace_count_lock:
            return self._trace_count

    def make_connection(self, **kwargs) -> "NIC500Connection":  # type: ignore[name-defined]
        """
        Convenience: return a NIC500Connection already pointed at this mock.
        Extra kwargs are forwarded to the constructor.
        """
        from spidar_gpr.SpidarGPR import NIC500Connection

        return NIC500Connection(
            ip=self.bind_ip,
            http_port=self.http_port,
            **kwargs,
        )
