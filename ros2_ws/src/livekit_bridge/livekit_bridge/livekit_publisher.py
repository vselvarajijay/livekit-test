"""ROS 2 node: subscribe to sensor_msgs/CompressedImage (H.264), decode, publish to LiveKit."""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import signal
import threading
from pathlib import Path
from typing import Optional

import av
import rclpy
from dotenv import load_dotenv
from livekit import api, rtc
from rclpy.node import Node
from sensor_msgs.msg import CompressedImage

LOGGER = logging.getLogger("livekit_bridge.publisher")

MAX_QUEUE_SIZE = 2


def _load_dotenv_from_repo() -> None:
    p = Path(__file__).resolve()
    for i in range(3, 10):
        if i >= len(p.parents):
            break
        candidate = p.parents[i] / ".env"
        if candidate.is_file():
            load_dotenv(candidate)
            LOGGER.info("Loaded environment from %s", candidate)
            return
    load_dotenv()


def _parse_topics(raw: str) -> list[str]:
    parts = [t.strip() for t in raw.split(",")]
    return [t for t in parts if t]


def _topic_to_track_name(topic: str) -> str:
    s = topic.strip().strip("/").replace("/", "_")
    return s if s else "video"


def _stamp_to_us(stamp) -> int:
    ns = int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)
    return ns // 1000


def _put_drop_oldest(q: queue.Queue, item) -> None:
    try:
        q.put_nowait(item)
    except queue.Full:
        try:
            q.get_nowait()
        except queue.Empty:
            pass
        try:
            q.put_nowait(item)
        except queue.Full:
            pass


class H264Decoder:
    """Incremental H.264 decoder using PyAV."""

    def __init__(self) -> None:
        self._codec = av.codec.CodecContext.create("h264", "r")

    def decode(self, data: bytes) -> list[av.VideoFrame]:
        if not data:
            return []
        packet = av.Packet(data)
        try:
            return self._codec.decode(packet)
        except av.AVError as e:
            LOGGER.debug("decode AVError (may need more data): %s", e)
            return []


def _i420_from_av_frame(frame: av.VideoFrame) -> rtc.VideoFrame:
    frame = frame.reformat(format="yuv420p")
    w, h = frame.width, frame.height
    p0, p1, p2 = frame.planes[0], frame.planes[1], frame.planes[2]
    cw, ch = w // 2, h // 2
    out = bytearray(w * h + 2 * cw * ch)

    ls0 = p0.line_size
    for row in range(h):
        base = row * ls0
        out[row * w : (row + 1) * w] = memoryview(p0)[base : base + w]

    offset = w * h
    ls1 = p1.line_size
    for row in range(ch):
        base = row * ls1
        out[offset + row * cw : offset + (row + 1) * cw] = memoryview(p1)[base : base + cw]
    offset += cw * ch

    ls2 = p2.line_size
    for row in range(ch):
        base = row * ls2
        out[offset + row * cw : offset + (row + 1) * cw] = memoryview(p2)[base : base + cw]

    return rtc.VideoFrame(w, h, rtc.VideoBufferType.I420, out)


def _publisher_token(
    api_key: str,
    api_secret: str,
    room: str,
    identity: str,
) -> str:
    return (
        api.AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_name("ros2_publisher")
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
            )
        )
        .to_jwt()
    )


async def _topic_worker(
    topic: str,
    track_name: str,
    pkt_q: queue.Queue,
    room: rtc.Room,
    shutdown: asyncio.Event,
) -> None:
    decoder = H264Decoder()
    source: Optional[rtc.VideoSource] = None

    def get_item():
        try:
            return pkt_q.get(timeout=0.25)
        except queue.Empty:
            return None

    while not shutdown.is_set():
        item = await asyncio.to_thread(get_item)
        if item is None:
            continue
        stamp, data = item
        frames = decoder.decode(data)
        for av_frame in frames:
            if source is None:
                w, h = av_frame.width, av_frame.height
                if w <= 0 or h <= 0:
                    continue
                source = rtc.VideoSource(w, h)
                track = rtc.LocalVideoTrack.create_video_track(track_name, source)
                options = rtc.TrackPublishOptions(
                    source=rtc.TrackSource.SOURCE_CAMERA,
                    simulcast=False,
                )
                try:
                    await room.local_participant.publish_track(track, options)
                    LOGGER.info("Published LiveKit track %s (%s) at %dx%d", track_name, topic, w, h)
                except rtc.ConnectError as e:
                    LOGGER.error("publish_track failed for %s: %s", topic, e)
                    return
                except Exception as e:
                    LOGGER.exception("publish_track failed for %s: %s", topic, e)
                    return

            assert source is not None
            vf = _i420_from_av_frame(av_frame)
            ts_us = _stamp_to_us(stamp) if stamp else 0
            source.capture_frame(vf, timestamp_us=ts_us)

    if source is not None:
        try:
            await source.aclose()
        except Exception as e:
            LOGGER.debug("VideoSource aclose: %s", e)


async def _run_livekit(
    topics: list[str],
    url: str,
    room_name: str,
    api_key: str,
    api_secret: str,
    identity: str,
    queues: dict[str, queue.Queue],
) -> None:
    loop = asyncio.get_running_loop()
    lk_room = rtc.Room(loop=loop)
    token = _publisher_token(api_key, api_secret, room_name, identity)

    @lk_room.on("disconnected")
    def _on_disconnected() -> None:
        LOGGER.warning("LiveKit disconnected")

    @lk_room.on("reconnecting")
    def _on_reconnecting() -> None:
        LOGGER.warning("LiveKit reconnecting")

    try:
        await lk_room.connect(url, token)
    except rtc.ConnectError as e:
        LOGGER.error("LiveKit connect failed: %s", e)
        return

    LOGGER.info("Connected to LiveKit room %s as %s", lk_room.name, identity)

    shutdown = asyncio.Event()
    tasks = []
    for topic in topics:
        tname = _topic_to_track_name(topic)
        tasks.append(
            asyncio.create_task(
                _topic_worker(topic, tname, queues[topic], lk_room, shutdown),
                name=f"worker:{topic}",
            )
        )

    try:
        while rclpy.ok():
            await asyncio.sleep(0.2)
    finally:
        shutdown.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await lk_room.disconnect()
        except Exception as e:
            LOGGER.debug("room.disconnect: %s", e)
        LOGGER.info("Disconnected from LiveKit")


class LiveKitPublisherNode(Node):
    """ROS node that queues CompressedImage payloads per topic."""

    def __init__(self, topics: list[str]) -> None:
        super().__init__("livekit_publisher")
        self._queues: dict[str, queue.Queue] = {}
        for t in topics:
            q: queue.Queue = queue.Queue(maxsize=MAX_QUEUE_SIZE)
            self._queues[t] = q
            self.create_subscription(
                CompressedImage,
                t,
                self._make_cb(t, q),
                10,
            )
            self.get_logger().info(
                f"Subscribed to {t} (sensor_msgs/CompressedImage)"
            )

    def _make_cb(self, topic: str, q: queue.Queue):
        def _cb(msg: CompressedImage) -> None:
            _put_drop_oldest(q, (msg.header.stamp, bytes(msg.data)))

        return _cb

    @property
    def topic_queues(self) -> dict[str, queue.Queue]:
        return self._queues


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    _load_dotenv_from_repo()

    url = os.environ.get("LIVEKIT_URL", "").strip()
    api_key = os.environ.get("LIVEKIT_API_KEY", "").strip()
    api_secret = os.environ.get("LIVEKIT_API_SECRET", "").strip()
    room_name = os.environ.get("LIVEKIT_ROOM", "robot").strip()
    identity = os.environ.get("LIVEKIT_PUBLISHER_IDENTITY", "robot").strip()
    raw_topics = os.environ.get("ROS2_VIDEO_TOPICS", "")
    topics = _parse_topics(raw_topics)

    if not url or not api_key or not api_secret:
        LOGGER.error("Set LIVEKIT_URL, LIVEKIT_API_KEY, and LIVEKIT_API_SECRET")
        raise SystemExit(1)
    if not topics:
        LOGGER.error("Set ROS2_VIDEO_TOPICS to a comma-separated list of topic names")
        raise SystemExit(1)

    LOGGER.info("LiveKit URL: %s, room: %s, topics: %s", url, room_name, topics)

    rclpy.init()
    node = LiveKitPublisherNode(topics)
    executor = rclpy.executors.MultiThreadedExecutor()
    executor.add_node(node)

    spin_thread = threading.Thread(target=executor.spin, daemon=True)
    spin_thread.start()

    def _handle_sigint(_sig, _frame) -> None:
        LOGGER.info("Shutting down (signal)")
        rclpy.shutdown()

    signal.signal(signal.SIGINT, _handle_sigint)
    signal.signal(signal.SIGTERM, _handle_sigint)

    try:
        asyncio.run(
            _run_livekit(
                topics,
                url,
                room_name,
                api_key,
                api_secret,
                identity,
                node.topic_queues,
            )
        )
    finally:
        executor.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        LOGGER.info("ROS 2 shutdown complete")


if __name__ == "__main__":
    main()
