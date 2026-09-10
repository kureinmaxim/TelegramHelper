"""Start the RNS bridge on the VPS.

Ties Reticulum request intake (DeviceControlBridge) to the local gRPC
DeviceControlService (GrpcCommandBackend). Transport (TCP now, I2P later)
is set by the RNS config in --config; bridge code does not change when
the interface changes.

Example:
    python -m bridge.run_bridge --grpc 127.0.0.1:50051 \
        --config ./_rnscfg --storage ./_rnsdata
"""
import argparse
import os
import threading
import time

from bridge.bridge import DeviceControlBridge
from bridge.grpc_backend import GrpcCommandBackend

# Re-announce interval, seconds (so clients can find a path to the bridge).
ANNOUNCE_INTERVAL = 60
HEARTBEAT_PATH = os.environ.get("HA_RNS_HEARTBEAT", "/tmp/ha-rns-bridge.heartbeat")
HEARTBEAT_EVERY = float(os.environ.get("HA_RNS_HEARTBEAT_EVERY", "20"))


def _heartbeat_loop() -> None:
    """Write an mtime file for ha-rns-watchdog (whole-process soft hang)."""
    while True:
        try:
            with open(HEARTBEAT_PATH, "w", encoding="utf-8") as f:
                f.write(f"{time.time():.3f}\n")
        except OSError:
            pass
        time.sleep(HEARTBEAT_EVERY)


def main() -> None:
    parser = argparse.ArgumentParser(description="RNS bridge for DeviceControlService")
    parser.add_argument("--grpc", default="127.0.0.1:50051",
                        help="local DeviceControlService address (gRPC)")
    parser.add_argument("--config", required=True, help="RNS configdir")
    parser.add_argument("--storage", required=True,
                        help="storagepath (holds the stable bridge identity)")
    parser.add_argument("--udp-target", default=None,
                        help="HOST:PORT UDP proxy for the /udp_raw path (optional)")
    args = parser.parse_args()

    udp_target = None
    if args.udp_target:
        host, _, port = args.udp_target.rpartition(":")
        udp_target = (host, int(port))

    backend = GrpcCommandBackend(args.grpc)
    bridge = DeviceControlBridge(args.config, args.storage, handler=backend,
                                 udp_target=udp_target,
                                 event_streamer=backend.subscribe_events)
    bridge.start()
    print(f"bridge up, destination = {bridge.destination.hash.hex()}", flush=True)
    print(f"grpc backend = {args.grpc}", flush=True)
    print(f"heartbeat = {HEARTBEAT_PATH} every {HEARTBEAT_EVERY}s", flush=True)
    print("event stream /subscribe enabled (RNS Channel)", flush=True)
    if udp_target:
        print(f"udp path /udp_raw enabled -> {udp_target[0]}:{udp_target[1]}", flush=True)
    else:
        print("udp path /udp_raw disabled (no --udp-target)", flush=True)

    threading.Thread(target=_heartbeat_loop, name="rns-heartbeat", daemon=True).start()

    while True:
        time.sleep(ANNOUNCE_INTERVAL)
        bridge.start()  # periodic re-announce


if __name__ == "__main__":
    main()
