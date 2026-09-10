"""RNS bridge: receives CommandRequest over Reticulum, returns CommandResponse.

Unary (stage 1): request handlers /device_control (proto) and /udp_raw (raw UDP).
Stream (stage 2): over RNS.Channel on an established Link — the client sends
SubscribeMessage; the bridge forwards the DeviceEvent stream (from event_streamer)
back as DeviceEventMessages.

Transport (TCP now, I2P later) is set by the RNS config (configdir); the code
is transport-agnostic."""
import os
import socket
import threading
import time
from typing import Callable, Iterable, Optional, Tuple

import RNS
from proto import device_control_pb2 as pb

APP_NAME = "apirgrpc"
ASPECTS = ("bridge", "devicecontrol")
REQUEST_PATH = "/device_control"
REQUEST_PATH_UDP = "/udp_raw"

# Soft hang: gRPC/RNS handler never returned — process dies, systemd Restart=always.
HANDLER_TIMEOUT_SEC = float(os.environ.get("HA_RNS_HANDLER_TIMEOUT", "25"))

CommandHandler = Callable[[pb.CommandRequest], pb.CommandResponse]
# event_streamer: given EventSubscribeRequest, yields a DeviceEvent stream (e.g. gRPC SubscribeEvents).
EventStreamer = Callable[[pb.EventSubscribeRequest], Iterable[pb.DeviceEvent]]


class SubscribeMessage(RNS.MessageBase):
    """Client → bridge: subscribe to events (data = serialized EventSubscribeRequest)."""
    MSGTYPE = 0x0021

    def __init__(self, data: bytes = b""):
        self.data = data

    def pack(self) -> bytes:
        return self.data

    def unpack(self, raw: bytes) -> None:
        self.data = raw or b""


class DeviceEventMessage(RNS.MessageBase):
    """Bridge → client: one event (data = serialized DeviceEvent)."""
    MSGTYPE = 0x0022

    def __init__(self, data: bytes = b""):
        self.data = data

    def pack(self) -> bytes:
        return self.data

    def unpack(self, raw: bytes) -> None:
        self.data = raw or b""


class DeviceControlBridge:
    def __init__(self, configdir: str, storagepath: str, handler: CommandHandler,
                 udp_target: Optional[Tuple[str, int]] = None,
                 udp_timeout: float = 5.0,
                 event_streamer: Optional[EventStreamer] = None):
        os.makedirs(storagepath, exist_ok=True)
        self._handler = handler
        self._udp_target = udp_target
        self._udp_timeout = udp_timeout
        self._event_streamer = event_streamer
        self.reticulum = RNS.Reticulum(configdir=configdir)
        id_path = os.path.join(storagepath, "bridge_identity")
        if os.path.isfile(id_path):
            self.identity = RNS.Identity.from_file(id_path)
        else:
            self.identity = RNS.Identity()
            self.identity.to_file(id_path)
        self.destination = RNS.Destination(self.identity, RNS.Destination.IN,
            RNS.Destination.SINGLE, APP_NAME, *ASPECTS)
        self.destination.register_request_handler(REQUEST_PATH,
            response_generator=self._on_request, allow=RNS.Destination.ALLOW_ALL)
        # Optional raw-UDP forwarding path.
        if self._udp_target is not None:
            self.destination.register_request_handler(REQUEST_PATH_UDP,
                response_generator=self._on_udp_request, allow=RNS.Destination.ALLOW_ALL)
        # Optional event-stream path (stage 2): set up Channel when a Link is established.
        if self._event_streamer is not None:
            self.destination.set_link_established_callback(self._on_link_established)

    def start(self) -> None:
        self.destination.announce()

    def _run_handler_with_timeout(self, req: pb.CommandRequest) -> pb.CommandResponse:
        """Call backend with a hard timeout — otherwise a soft hang holds the client forever."""
        box: dict = {}

        def run() -> None:
            try:
                box["resp"] = self._handler(req)
            except Exception as exc:  # noqa: BLE001 — return to the client, do not kill the RNS thread
                box["err"] = exc

        t = threading.Thread(target=run, name="dc-handler", daemon=True)
        t.start()
        t.join(HANDLER_TIMEOUT_SEC)
        if t.is_alive():
            RNS.log(
                f"device_control handler hung >{HANDLER_TIMEOUT_SEC}s — exit for systemd restart",
                RNS.LOG_ERROR,
            )
            # Immediate exit: systemd Restart=always will bring the bridge back up.
            os._exit(78)
        if "err" in box:
            return pb.CommandResponse(
                status=pb.CommandResponse.ERROR,
                message=f"handler error: {box['err']}",
            )
        return box["resp"]

    # ---- unary -------------------------------------------------------------
    def _on_request(self, path, data, request_id, link_id, remote_identity, requested_at):
        req = pb.CommandRequest()
        try:
            req.ParseFromString(data or b"")
        except Exception as exc:
            return pb.CommandResponse(status=pb.CommandResponse.ERROR,
                                      message=f"bad request: {exc}").SerializeToString()
        return self._run_handler_with_timeout(req).SerializeToString()

    def _on_udp_request(self, path, data, request_id, link_id, remote_identity, requested_at):
        """Forward raw bytes to the UDP proxy target and return the single reply
        (b"" on timeout/error, so the Link is not torn down)."""
        host, port = self._udp_target
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(self._udp_timeout)
            sock.sendto(data or b"", (host, port))
            reply, _addr = sock.recvfrom(65535)
            return reply
        except OSError:
            return b""
        finally:
            sock.close()

    # ---- streaming (stage 2) ------------------------------------------------
    def _on_link_established(self, link) -> None:
        channel = link.get_channel()
        channel.register_message_type(SubscribeMessage)
        channel.register_message_type(DeviceEventMessage)
        channel.add_message_handler(lambda msg: self._on_channel_message(channel, msg))

    def _on_channel_message(self, channel, message) -> bool:
        if isinstance(message, SubscribeMessage):
            req = pb.EventSubscribeRequest()
            try:
                req.ParseFromString(message.data or b"")
            except Exception:
                req = pb.EventSubscribeRequest()
            threading.Thread(target=self._forward_events, args=(channel, req), daemon=True).start()
            return True
        return False

    def _forward_events(self, channel, req: pb.EventSubscribeRequest) -> None:
        try:
            for event in self._event_streamer(req):
                # backpressure: wait for the channel send window
                deadline = time.time() + 10
                while not channel.is_ready_to_send() and time.time() < deadline:
                    time.sleep(0.05)
                channel.send(DeviceEventMessage(event.SerializeToString()))
        except Exception as exc:
            RNS.log(f"event stream ended/failed: {exc}", RNS.LOG_ERROR)
