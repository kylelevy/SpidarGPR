# 📡 SpidarGPR — NIC-500 Ground Penetrating Radar Interface

A Python-based interface for interacting with the **Spidar NIC-500 Ground Penetrating Radar (GPR)** system in SDK mode. This module provides a clean, modular, and thread-safe API for **data acquisition, streaming, and integration with ROS2 pipelines**. 

It directly operated on top of the interfaces provided by the developer SDK.

- **Spidar SDK Docs:** https://sensoftinc.github.io/spidar-sdk/index.html
- **Spidar SDK Repo:** https://github.com/SensoftInc/spidar-sdk

---

## 🚀 Features

* 🔌 Full SDK-based control of NIC-500 hardware
* 🧵 Thread-safe continuous streaming interface
* 📦 Structured trace representation using Python dataclasses
* 🔄 Context-managed sessions for safe hardware interaction
* 📦 Installable as a local Python package (pip install .)

---

⚙️ Installation

This project is not published on PyPI. Install it locally from the repository.

1. Clone the Repository
```bash
git clone https://github.com/kylelevy/SpidarGPR.git
cd SpidarGPR
```
2. Install as a Package
```bash
pip install .
```

For development (editable install):

```bash
pip install -e .[dev]
```

---

## Setup

## ⚙️ Requirements

### Software Dependencies

* Python 3.8+
* Required packages:

  ```bash
  pip install numpy requests
  ```

### Hardware Requirements

* **Spidar NIC-500 GPR system**
* Device must be:

  * Connected via **Ethernet**
  * Running in **SDK mode**

---

## 🧠 System Overview

The system is built around a **connection manager abstraction** that ensures:

* Safe hardware communication
* Modular extensibility
* Concurrent data acquisition

### Core Components

#### 1. `NIC500Connection`

Main interface class responsible for:

* Hardware communication via HTTP API
* Data streaming via TCP socket
* Session lifecycle management

#### 2. `GPRTrace` Dataclass

Represents a single radar trace:

```python
@dataclass(frozen=True)
class GPRTrace:
    tv_sec: int
    tv_nsec: int
    trace_num: int
    status: int
    stacks: int
    header_size: int
    data: np.ndarray
```

---

## 🔌 Connection Workflow

A typical session follows this sequence:

1. Verify SDK mode
2. Retrieve system information
3. Power on GPR
4. Configure acquisition parameters
5. Open data socket
6. Start acquisition

All of this is handled automatically via a **context manager**.

---

## 🧪 Basic Usage

### 📥 Fixed Trace Acquisition

```python
from spidar_gpr import NIC500Connection

nic = NIC500Connection()

with nic.session():
    traces = nic.read_traces(10)
```

---

### 🔄 Continuous Streaming

```python
from spidar_gpr import NIC500Connection
import time

nic = NIC500Connection()

with nic.session():
    nic.start_streaming()

    # Collect data for 2 seconds
    import time
    time.sleep(2)

    traces = nic.get_latest_traces()
    print(f"Collected {len(traces)} traces")

    nic.stop_streaming()
```

---

## 📡 Data Streaming Model

* Data is received via TCP socket from NIC-500
* Each trace consists of:

  * Header (metadata)
  * Signal samples (float32 array)
* Internal buffering:
  * Thread-safe `deque`
  * Background reader thread

---

## 🧵 Thread Safety

* Uses `threading.Lock` for buffer access

---

## 🧰 Context Managers

The system uses layered context managers for safe operation:

### `session()`

Top-level manager:

* Initializes system
* Configures GPR
* Starts acquisition

### `data_socket_context()`

* Opens/closes TCP socket

### `acquisition()`

* Starts/stops hardware acquisition

✅ Ensures cleanup even on exceptions

---

## 📊 Visualization Example

```python
def plot_radargram_from_traces(traces):
    import numpy as np
    import matplotlib.pyplot as plt

    radargram = np.vstack([t["data"] for t in traces])

    plt.imshow(radargram.T, aspect="auto", cmap="seismic")
    plt.colorbar(label="Amplitude (mV)")
    plt.xlabel("Trace Number")
    plt.ylabel("Time")
    plt.title("GPR Radargram")
    plt.show()
```

---

## ⚠️ Error Handling

Custom exceptions:

* `NICModeError` — Device not in SDK mode
* `GPRPowerOnError` — Power failure
* `DataSocketError` — Socket issues
* `SetupError` — Configuration failure
* `AcquisitionError` — Acquisition failure

---

## 🔧 Configuration Parameters

Defined as inputs to the class with default values:

| Parameter                   | Value   | Description             |
| --------------------------- | ------- | ----------------------- |
| `POINTS_PER_TRACE`          | 200     | Samples per trace       |
| `TIME_SAMPLING_INTERVAL_PS` | 100 ps  | Sampling resolution     |
| `POINT_STACKS`              | 4       | Signal averaging        |
| `PERIOD_S`                  | 0.00125 | 800 Hz acquisition rate |

---

## 🧪 Development Notes

* Default IP address:

  ```
  192.168.20.221
  ```

---

## 📄 License

**GNU Lesser General Public License**

- ✅ Allows commercial use
- ✅ Only requires changes to your code to be open-source (not the whole project)
- ✅ Includes patent protections
- ✅ Widely used and understood

---

## 👨‍💻 Author

**Kyle Levy**
- 📧 [kylerlevy@gmail.com](mailto:kylerlevy@gmail.com)
- 🔗 GitHub: [https://github.com/kylelevy/SpidarGPR](https://github.com/kylelevy/SpidarGPR)

---

## 📚 References

* Spidar NIC-500 SDK Documentation

---

Here’s a clean section you can add to your README to document the test suite and how to run it:

---

## 🧪 Test Suite

This project includes a **pytest-based test suite** located in the `tests/` directory.

### 📦 Install Test Dependencies

If you installed the package normally, make sure `pytest` is available:

```bash
pip install pytest
```

---

### ▶️ Running Tests

From the root of the repository:

```bash
pytest
```

This will automatically discover and run all tests inside the `tests/` folder.

---

### 🔍 Running Specific Tests

Run a single test file:

```bash
pytest tests/test_connection.py
```

Run a specific test function:

```bash
pytest tests/test_connection.py::test_read_traces
```

---

### 🧪 Notes

* The mock simulates:

  * Full HTTP API surface used by `NIC500Connection`
  * TCP data socket streaming real binary trace packets
  * Acquisition state transitions and configuration handling

* Key testing capabilities provided by the mock:

  * ✅ Run tests entirely offline (no hardware required)
  * ✅ Validate full session lifecycle (`session()`, acquisition, teardown)
  * ✅ Simulate edge cases:

    * SDK mode disabled (triggers `NICModeError`)
    * Dropped socket connections (`drop_after`)
    * Variable trace rates (`trace_delay_s`)

  * ✅ Inspect internal behavior:

    * Last setup payload (`last_setup_payload`)
    * Acquisition start/stop transitions (`acquisition_transitions`)
    * Total traces streamed
