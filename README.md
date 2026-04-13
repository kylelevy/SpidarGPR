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

---

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
from SpidarGPR import NIC500Connection

nic = NIC500Connection()

with nic.session():
    traces = nic.read_traces(10)
```

---

### 🔄 Continuous Streaming

```python
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
* Safe concurrent reads via:

  ```python
  get_latest_traces(clear=True)
  ```

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

Defined as class constants:

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
* Modify `IP_ADDRESS` if needed:

  ```python
  NIC500Connection.IP_ADDRESS = "your_device_ip"
  ```

---

## 🛠 Future Improvements

* ROS2 native node integration
* Async I/O instead of threading
* Data compression for high-rate streaming

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
