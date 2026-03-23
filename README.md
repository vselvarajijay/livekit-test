# ROS 2 → LiveKit video MVP

One-way video from ROS 2 `sensor_msgs/msg/CompressedImage` topics (H.264 payloads) to a web browser via [LiveKit](https://livekit.io/). The Python bridge decodes H.264 with PyAV, pushes I420 frames into LiveKit’s `VideoSource`, and the operator UI subscribes to remote camera tracks in a grid layout.

## Prerequisites

- **ROS 2** (Humble or newer) with `rclpy` and `sensor_msgs`
- **Docker** (for local LiveKit)
- **Python 3.10+** and [**uv**](https://docs.astral.sh/uv/getting-started/installation/) (Python package manager)
- **Node.js 20+** (for the operator UI)

## 1. Run LiveKit locally (Docker)

The server must expose the WebSocket port and UDP ports for WebRTC. Example using dev keys (matches the sample `.env.example` values):

```bash
docker run --rm \
  -p 7880:7880 \
  -p 7881:7881 \
  -p 7882:7882/udp \
  -p 50000-50100:50000-50100/udp \
  -e LIVEKIT_KEYS="devkey: devsecretdevsecretdevsecretdevsecret" \
  livekit/livekit-server \
  --dev
```

- **API key:** `devkey`
- **API secret:** `devsecretdevsecretdevsecretdevsecret` (must match what you put in `.env`)

If you change `LIVEKIT_KEYS`, update `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` in your `.env` to match (`key: secret` pairs are colon-separated in `LIVEKIT_KEYS`).

## 2. Environment

Copy the sample file and edit values:

```bash
cp .env.example .env
```

Required variables:

| Variable | Description |
|----------|-------------|
| `LIVEKIT_URL` | WebSocket URL, e.g. `ws://127.0.0.1:7880` |
| `LIVEKIT_API_KEY` | Server API key |
| `LIVEKIT_API_SECRET` | Server API secret |
| `LIVEKIT_ROOM` | Room name shared by publisher and browser |
| `ROS2_VIDEO_TOPICS` | Comma-separated ROS topic names (`sensor_msgs/CompressedImage`) |

Optional:

| Variable | Description |
|----------|-------------|
| `LIVEKIT_PUBLISHER_IDENTITY` | LiveKit participant identity for the robot (default `robot`) |
| `ROS_DOMAIN_ID` | DDS domain (standard ROS 2 env; not read by the node but affects discovery) |

For the **frontend** dev server, copy [`frontend/.env.example`](frontend/.env.example) to `frontend/.env` and set `VITE_LIVEKIT_URL` if you want a default WebSocket URL in the form.

## 3. Python dependencies (bridge)

Runtime dependencies are listed in [`setup.py`](ros2_ws/src/livekit_bridge/setup.py) (`install_requires`) and mirrored in [`requirements.txt`](ros2_ws/src/livekit_bridge/requirements.txt). This package uses **classic `setup.py` only** (no `pyproject.toml` `[project]` metadata): `colcon` introspects `setup.py` in a way that breaks on setuptools’ `python_requires` / PEP 621 `requires-python` (`SpecifierSet` is not `literal_eval`-safe).

Install into the **same Python interpreter you use with ROS 2** (after sourcing your ROS setup):

```bash
source /opt/ros/$ROS_DISTRO/setup.bash   # adjust for your install
cd ros2_ws/src/livekit_bridge
uv pip install -e .
```

Or install from the requirements file:

```bash
uv pip install -r requirements.txt
```

Optional: a local venv so `rclpy` from the system ROS install remains importable:

```bash
source /opt/ros/$ROS_DISTRO/setup.bash
cd ros2_ws/src/livekit_bridge
uv venv --system-site-packages --python "$(which python3)"
source .venv/bin/activate
uv pip install -e .
```

## 4. Build and run the ROS 2 publisher

```bash
cd ros2_ws
source /opt/ros/$ROS_DISTRO/setup.bash   # adjust path for your install
colcon build --packages-select livekit_bridge
source install/setup.bash
ros2 run livekit_bridge livekit_publisher
```

Ensure your robot (or a bag/player) publishes **H.264** byte streams on `ROS2_VIDEO_TOPICS` as `sensor_msgs/msg/CompressedImage`.

## 5. Access tokens (JWT)

Do **not** put API secrets in the browser. Generate short-lived JWTs with `livekit-api`:

### Publisher (can publish video)

```bash
python3 -c "
from livekit import api
import os
os.environ.setdefault('LIVEKIT_API_KEY', 'devkey')
os.environ.setdefault('LIVEKIT_API_SECRET', 'devsecretdevsecretdevsecretdevsecret')
print(api.AccessToken() \
  .with_identity('robot') \
  .with_grants(api.VideoGrants(room_join=True, room='robot', can_publish=True, can_subscribe=True)) \
  .to_jwt())
"
```

The node also generates this internally when it starts; the snippet is useful for debugging.

### Operator UI (subscribe only)

```bash
python3 -c "
from livekit import api
import os
os.environ.setdefault('LIVEKIT_API_KEY', 'devkey')
os.environ.setdefault('LIVEKIT_API_SECRET', 'devsecretdevsecretdevsecretdevsecret')
print(api.AccessToken() \
  .with_identity('operator') \
  .with_grants(api.VideoGrants(room_join=True, room='robot', can_publish=False, can_subscribe=True)) \
  .to_jwt())
"
```

Use `LIVEKIT_ROOM` in place of `'robot'` if you changed it.

## 6. Run the operator UI

```bash
cd frontend
npm install
npm run dev
```

Open the printed URL, enter the **LiveKit URL** (e.g. `ws://127.0.0.1:7880`) and paste the **operator** JWT. You should see one tile per camera track published by the robot participant.

## Architecture note

The LiveKit Python `VideoFrame` API accepts **uncompressed** buffers (e.g. I420). Pre-encoded H.264 from ROS is **decoded** on the bridge, then sent through the normal WebRTC video pipeline (re-encoded for transport). For true end-to-end H.264 passthrough you would need a different ingress path (not this MVP).

## Repository layout

- [`ros2_ws/src/livekit_bridge/`](ros2_ws/src/livekit_bridge/) — ROS 2 Python package and `livekit_publisher` entry point
- [`frontend/`](frontend/) — Vite + React operator UI
