import time
import socket
import struct
import json
import requests
import threading
import numpy as np
from collections import deque
from dataclasses import dataclass
from contextlib import contextmanager


# =========================
# Custom Exceptions
# =========================


class NICModeError(Exception):
    pass


class GPRPowerOnError(Exception):
    pass


class GPRSystemInfoFetchError(Exception):
    pass


class DataSocketError(Exception):
    pass


class SetupError(Exception):
    pass


class AcquisitionError(Exception):
    pass


# =========================
# ROS GPR Dataclass
# =========================


@dataclass(frozen=True)
class GPRTrace:
    tv_sec: int
    tv_nsec: int
    trace_num: int
    status: int
    stacks: int
    header_size: int
    data: np.ndarray


# =========================
# Connection Manager Class
# =========================


class NIC500Connection:
    """
    Context-managed connection manager for a NIC-500 running in SDK mode.
    """

    # =========================
    # Constants
    # =========================

    POWER_ON_CONFIGURATION = {"data": json.dumps({"state": 2})}
    START_ACQUISITION_CONFIGURATION = {"data": json.dumps({"state": 1})}
    STOP_ACQUISITION_CONFIGURATION = {"data": json.dumps({"state": 0})}

    HEADER_SIZE_BYTES = 20
    POINT_SIZE_BYTES = 4

    # =========================
    # Initialization
    # =========================

    def __init__(
        self,
        ip: str = "192.168.20.221",
        points_per_trace: int = 200,
        time_sampling_interval_ps: int = 100,
        point_stacks: int = 4,
        period_s: int = 0,
        first_break_point: int = 20,
    ):
        """
        Initialize a connection configuration for a NIC-500 device operating in SDK mode.

        This constructor sets up API endpoints, acquisition parameters, and internal
        buffers used for reading and streaming GPR trace data. It does not establish
        a network connection until used within the provided context managers.

        Args:
            ip (str, optional):
                IP address of the NIC-500 device. Defaults to "192.168.20.221".

            points_per_trace (int, optional):
                Number of sampled data points per GPR trace. Determines the size
                of each trace payload. Defaults to 200.

            time_sampling_interval_ps (int, optional):
                Time interval between consecutive samples in picoseconds (ps).
                Controls temporal resolution of the trace. Defaults to 100.

            point_stacks (int, optional):
                Number of stacks (averages) per point to improve signal quality.
                Higher values reduce noise but increase acquisition time.
                Defaults to 4.

            period_s (int, optional):
                Acquisition period in seconds. Defines the interval between
                consecutive traces (1 / period_s Hz). A value of 0 typically
                means continuous acquisition. Defaults to 0.

            first_break_point (int, optional):
                Index of the first break point used to adjust the time window
                shift. This affects alignment of trace data in time.
                Defaults to 20.

        Attributes:
            API_URL (str): Base URL for NIC-500 REST API.
            data_socket (socket.socket | None): Active TCP socket for trace streaming.
            gpr_system_info (dict | None): Cached GPR system information.
            window_time_shift_ps (int): Computed time shift applied to traces.
        """
        self.ip = ip

        self.API_URL = f"http://{self.ip}:8080/api"
        self.NIC_SYSTEM_INFO_CMD = self.API_URL + "/nic/system_information"
        self.GPR_SYSTEM_INFO_CMD = self.API_URL + "/nic/gpr/system_information"
        self.DATA_SOCKET_CMD = self.API_URL + "/nic/gpr/data_socket"
        self.VERSION_CMD = self.API_URL + "/nic/version"
        self.POWER_CMD = self.API_URL + "/nic/power"
        self.SETUP_CMD = self.API_URL + "/nic/setup"
        self.ACQUISITION_CMD = self.API_URL + "/nic/acquisition"

        self.POINTS_PER_TRACE = points_per_trace
        self.TIME_SAMPLING_INTERVAL_PS = time_sampling_interval_ps
        self.POINT_STACKS = point_stacks
        self.PERIOD_S = period_s  # 1/period_s Hz
        self.FIRST_BREAK_POINT = first_break_point

        self.data_socket = None
        self.gpr_system_info = None
        self.window_time_shift_ps = -55000

        # Streaming traces
        self._trace_buffer = deque(maxlen=None)  # rolling buffer
        self._buffer_lock = threading.Lock()
        self._streaming = False
        self._reader_thread = None

    # =========================
    # Internal HTTP helpers
    # =========================

    def _get_requests(self, command, command_str_name):
        """
        Perform a get request to retrieve data from a resource.
        @param command: URL for the request command
        @type command: str
        @param command_str_name: Name of the command
        @type command_str_name: str
        @return: response in json format
        @rtype: JSON or None when request failed
        """
        json_response = None
        try:
            response = requests.get(command)
            json_response = json.loads(response.content)
            print(
                "Response from {} command: {}\n".format(
                    command_str_name, json_response["status"]["message"]
                )
            )
        except ValueError as err:
            print("Unable to decode JSON: {}\n".format(err))
        except KeyError:
            print(
                "Response from {} command: {}\n".format(
                    command_str_name, json_response["success"]
                )
            )
        return json_response

    def _put_requests(self, command, data, command_str_name):
        """
        Perform a put request to change the resource’s data.
        @param command: URL for the request command
        @type command: str
        @param data: Data to send to the command
        @type data: dict
        @param command_str_name: Name of the command
        @type command_str_name: str
        @return: response in json format
        @rtype: JSON or None when request failed
        """
        json_response = None
        try:
            response = requests.put(command, data=data)
            json_response = json.loads(response.content)
            print(
                "Response from {} command: {}\n".format(
                    command_str_name, json_response["status"]["message"]
                )
            )
        except ValueError as err:
            print("Unable to decode JSON: {}\n".format(err))

        if json_response["status"]["status_code"] != 0:
            print("Command failed: {}".format(json_response["status"]["message"]))
            json_response = None

        return json_response

    # =========================
    # Public API
    # =========================

    def check_sdk_mode(self):
        api_response = self._get_requests(self.API_URL, "API")
        if api_response["data"]["name"] != "NIC-500 SDK":
            raise NICModeError("NIC is not in SDK mode")
        print("NIC is in SDK mode")

    def get_system_information(self):
        response = self._get_requests(
            self.NIC_SYSTEM_INFO_CMD, "NIC System Information"
        )
        info = response["data"]
        print(
            f"System Information:\n"
            f"  App DIP: {info['app_dip']}\n"
            f"  App Version: {info['app_version']}\n"
            f"  FPGA Version: {info['fpga_version']}\n"
            f"  Hardware ID: {info['hardware_id']}\n"
            f"  Kernel Version: {info['kernel_version']}\n"
            f"  NIC Serial: {info['nic_serial_number']}\n"
            f"  OS Version: {info['os_version']}\n"
        )

    def turn_on_GPR(self):
        self._put_requests(self.POWER_CMD, self.POWER_ON_CONFIGURATION, "Power")

    def get_GPR_information(self):
        response = self._get_requests(
            self.GPR_SYSTEM_INFO_CMD, "GPR System Information"
        )
        self.gpr_system_info = response["data"]["gpr"]

        ref = self.gpr_system_info.get("window_time_shift_reference_ps")
        if ref is not None:
            self.window_time_shift_ps = int(ref) - (
                self.FIRST_BREAK_POINT * self.TIME_SAMPLING_INTERVAL_PS
            )

        print(f"GPR Window Time Shift (ps): {self.window_time_shift_ps}")

    def setup_gpr(self):
        config = {
            "data": json.dumps(
                {
                    "gpr0": {
                        "parameters": {
                            "points_per_trace": self.POINTS_PER_TRACE,
                            "window_time_shift_ps": self.window_time_shift_ps,
                            "point_stacks": self.POINT_STACKS,
                            "time_sampling_interval_ps": self.TIME_SAMPLING_INTERVAL_PS,
                        }
                    },
                    "timer": {"parameters": {"period_s": self.PERIOD_S}},
                }
            )
        }
        self._put_requests(self.SETUP_CMD, config, "Setup")

    # =========================
    # Context Managers
    # =========================

    @contextmanager
    def data_socket_context(self):
        """
        Context manager for the GPR data socket.
        """
        response = self._get_requests(self.DATA_SOCKET_CMD, "Data Socket")
        port = response["data"]["data_socket"]["port"]

        try:
            self.data_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.data_socket.connect((self.ip, port))
            yield self.data_socket
        except Exception as exc:
            raise DataSocketError(f"Failed to use data socket: {exc}")
        finally:
            if self.data_socket:
                self.data_socket.close()
                self.data_socket = None

    @contextmanager
    def acquisition(self):
        """
        Context manager for acquisition start/stop.
        """
        try:
            self._put_requests(
                self.ACQUISITION_CMD,
                self.START_ACQUISITION_CONFIGURATION,
                "Start Acquisition",
            )
            yield
        finally:
            self._put_requests(
                self.ACQUISITION_CMD,
                self.STOP_ACQUISITION_CONFIGURATION,
                "Stop Acquisition",
            )

    @contextmanager
    def session(self):
        """
        High-level context manager for a complete GPR session.
        """
        self.check_sdk_mode()
        self.get_system_information()
        self.turn_on_GPR()
        self.get_GPR_information()
        self.setup_gpr()

        with self.data_socket_context():
            with self.acquisition():
                yield self

    # =========================
    # Data Reading
    # =========================

    def read_traces(self, max_traces: int = 1024):
        if not self.data_socket:
            raise DataSocketError("Data socket is not open")

        traces = []
        trace_size = self.HEADER_SIZE_BYTES + (
            self.POINTS_PER_TRACE * self.POINT_SIZE_BYTES
        )

        buffer = bytearray()

        while len(traces) < max_traces:
            buffer += self.data_socket.recv(trace_size)

            while len(buffer) >= trace_size:
                header = buffer[: self.HEADER_SIZE_BYTES]
                samples = buffer[self.HEADER_SIZE_BYTES : trace_size]
                del buffer[:trace_size]

                tv_sec, tv_nsec, trace_num, status, stacks, header_size = struct.unpack(
                    "<LLLLHH", header
                )

                points = np.frombuffer(samples, dtype=np.float32)

                print(
                    f"Trace {trace_num}: "
                    f"tv_sec={tv_sec}, status={status}, stacks={stacks}"
                )
                print("First 10 points (mV):", points[:10], "\n")

                traces.append(
                    {
                        "header": {
                            "tv_sec": tv_sec,
                            "tv_nsec": tv_nsec,
                            "trace_num": trace_num,
                            "status": status,
                            "stacks": stacks,
                            "header_size": header_size,
                        },
                        "data": points,
                    }
                )

        return traces

    # =========================
    # Data Streaming
    # =========================

    def start_streaming(self):
        if not self.data_socket:
            raise DataSocketError("Data socket is not open")

        if self._streaming:
            return

        self._streaming = True
        self._reader_thread = threading.Thread(
            target=self._trace_reader_loop, daemon=True
        )
        self._reader_thread.start()

    def stop_streaming(self):
        self._streaming = False
        if self._reader_thread:
            self._reader_thread.join(timeout=1.0)
            self._reader_thread = None

    def get_latest_traces(self, clear: bool = True):
        """
        Retrieve buffered traces in a thread-safe way.

        Args:
            clear (bool): Clear buffer after read

        Returns:
            list[GPRTrace] | None
        """
        if len(self._trace_buffer) == 0:
            return

        with self._buffer_lock:
            # print("DEBUG: len_traces = "+str(len(self._trace_buffer)))
            traces = [
                self._trace_buffer.popleft() for _ in range(len(self._trace_buffer))
            ]
            if clear:
                self._trace_buffer.clear()

        return traces

    def _trace_reader_loop(self):
        trace_size = self.HEADER_SIZE_BYTES + (
            self.POINTS_PER_TRACE * self.POINT_SIZE_BYTES
        )

        buffer = bytearray()

        while self._streaming:
            try:
                chunk = self.data_socket.recv(trace_size)
                if not chunk:
                    time.sleep(0.001)
                    continue

                buffer += chunk

                while len(buffer) >= trace_size:
                    header = buffer[: self.HEADER_SIZE_BYTES]
                    samples = buffer[self.HEADER_SIZE_BYTES : trace_size]
                    del buffer[:trace_size]

                    tv_sec, tv_nsec, trace_num, status, stacks, header_size = (
                        struct.unpack("<LLLLHH", header)
                    )

                    points = np.frombuffer(samples, dtype=np.float32)

                    trace = GPRTrace(
                        tv_sec=tv_sec,
                        tv_nsec=tv_nsec,
                        trace_num=trace_num,
                        status=status,
                        stacks=stacks,
                        header_size=header_size,
                        data=points,
                    )

                    # thread-safe append
                    with self._buffer_lock:
                        self._trace_buffer.append(trace)

            except Exception as exc:
                print(f"Trace reader error: {exc}")
                time.sleep(0.01)


if __name__ == "__main__":
    nic = NIC500Connection()

    # Fixed triggering
    with nic.session():
        traces = nic.read_traces(10)

    def plot_radargram_from_traces(traces):
        """
        Plot a GPR radargram from read_traces() output.

        Args:
            traces (list[dict]): Output of read_traces()
        """
        if not traces:
            print("No traces to plot.")
            return

        import matplotlib.pyplot as plt

        # Stack trace data into 2D array: (num_traces, points_per_trace)
        radargram = np.vstack([t["data"] for t in traces])

        time_axis_ns = (
            np.arange(radargram.shape[1]) * nic.TIME_SAMPLING_INTERVAL_PS * 1e-3
        )
        trace_axis = np.arange(radargram.shape[0])

        plt.figure(figsize=(10, 6))
        plt.imshow(
            radargram.T,
            aspect="auto",
            cmap="seismic",
            extent=[trace_axis[0], trace_axis[-1], time_axis_ns[-1], time_axis_ns[0]],
        )

        plt.colorbar(label="Amplitude (mV)")
        plt.xlabel("Trace Number")
        plt.ylabel("Two-Way Travel Time (ns)")
        plt.title("GPR Radargram")
        plt.tight_layout()
        plt.show()

    plot_radargram_from_traces(traces)

    # Continuous triggering
    with nic.session():
        nic.start_streaming()

        time.sleep(2.0)  # collect for a bit

        traces = nic.get_latest_traces()
        print(f"Collected {len(traces)} traces")

        nic.stop_streaming()
