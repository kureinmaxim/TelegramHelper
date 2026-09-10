"""HA stub gRPC server — simulates an "HA server" with connected Mi-Home
devices (NO real Home Assistant). Implements DeviceControlService.SendCommand
with deterministic device stubs:

  mi_bulb       — smart bulb (LED): WRITE on/off, READ state
  mi_th_sensor  — temperature/humidity sensor: READ
  mi_vibration  — vibration sensor: READ
  __ping__      — health-check (Ping button in ApiRgRPC), same as ha-adapter
  __list__      — JSON device list in read_data (dropdown in ApiRgRPC)

Readings go into existing CommandResponse fields (message + device_state);
the proto is NOT changed. Listens on 127.0.0.1 only (access via SSH tunnel).

Run:  python stub_server.py --listen 127.0.0.1:50055
"""
import argparse
import json
import os
import sys
import time
from concurrent import futures

import grpc

# Make the proto/ package importable (bundle-local).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from proto import device_control_pb2 as pb            # noqa: E402
from proto import device_control_pb2_grpc as pbg      # noqa: E402

# Special IDs matching ApiRgRPC ha_adapter (GUI Ping / device list).
PING_DEVICE_ID = "__ping__"
LIST_DEVICE_ID = "__list__"

# Minimal in-memory state so WRITE on/off is visible in the next READ.
_BULB = {"is_on": False, "brightness": 80}


class HaStubService(pbg.DeviceControlServiceServicer):
    """DeviceControlService stub: routes by device_id to Mi-Home devices."""

    def SendCommand(self, request, context):
        device = request.device_id
        if device == PING_DEVICE_ID:
            return pb.CommandResponse(
                status=pb.CommandResponse.SUCCESS,
                message="stub gRPC alive",
            )
        if device == LIST_DEVICE_ID:
            return self._list_devices()
        if device == "mi_bulb":
            return self._bulb(request)
        if device == "mi_th_sensor":
            return self._th_sensor(request)
        if device == "mi_vibration":
            return self._vibration(request)
        return pb.CommandResponse(
            status=pb.CommandResponse.ERROR,
            message=f"unknown device: {device}",
        )

    def _list_devices(self):
        """Same format as ha-adapter: {"backend","devices":[{id,entity,state,name,writable}]}."""
        power = "on" if _BULB["is_on"] else "off"
        items = [
            {
                "id": "mi_bulb",
                "entity": "mi_bulb",
                "state": power,
                "name": "Stub Mi Bulb",
                "writable": True,
                "alias": True,
            },
            {
                "id": "mi_th_sensor",
                "entity": "mi_th_sensor",
                "state": "23.5",
                "name": "Stub TH Sensor",
                "writable": False,
                "alias": True,
            },
            {
                "id": "mi_vibration",
                "entity": "mi_vibration",
                "state": "idle",
                "name": "Stub Vibration",
                "writable": False,
                "alias": True,
            },
        ]
        payload = json.dumps(
            {"backend": "stub", "devices": items}, ensure_ascii=False
        ).encode("utf-8")
        return pb.CommandResponse(
            status=pb.CommandResponse.SUCCESS,
            message=f"stub: {len(items)} device(s)",
            read_data=payload,
        )

    def _bulb(self, req):
        if req.command_code == pb.CommandCode.WRITE and req.HasField("set_led_state"):
            _BULB["is_on"] = bool(req.set_led_state.state)
        node = pb.DeviceNode(id="mi_bulb", block=req.block_type,
                             type=pb.DeviceType.DEVICETYPE_LED)
        node.led_state.is_on = _BULB["is_on"]
        node.parameters["brightness"] = str(_BULB["brightness"])
        power = "on" if _BULB["is_on"] else "off"
        return pb.CommandResponse(
            status=pb.CommandResponse.SUCCESS,
            message=f"mi_bulb: power={power}, brightness={_BULB['brightness']}",
            device_state=node,
        )

    def _th_sensor(self, req):
        node = pb.DeviceNode(id="mi_th_sensor", block=req.block_type,
                             type=pb.DeviceType.DEVICETYPE_TEMPERATURE)
        node.temperature_state.temperature_celsius = 23.5
        node.parameters["humidity"] = "45"
        return pb.CommandResponse(
            status=pb.CommandResponse.SUCCESS,
            message="mi_th_sensor: T=23.5C H=45%",
            device_state=node,
        )

    def _vibration(self, req):
        node = pb.DeviceNode(id="mi_vibration", block=req.block_type,
                             type=pb.DeviceType.DEVICETYPE_UNKNOWN)
        node.parameters["vibration"] = "idle"
        node.generic_state_data = b"\x00"
        return pb.CommandResponse(
            status=pb.CommandResponse.SUCCESS,
            message="mi_vibration: state=idle, last_event=none",
            device_state=node,
        )

    def SubscribeEvents(self, request, context):
        """Stub stream (stage 2): 5 periodic mi_th_sensor events."""
        for i in range(5):
            temp = 23.5 + i * 0.1
            yield pb.DeviceEvent(
                block_type=request.block_type,
                device_id="mi_th_sensor",
                event_type=pb.EventType.EVENT_UNKNOWN,
                timestamp_unix_ms=int(time.time() * 1000),
                payload=f"T={temp:.1f}C H=45%".encode(),
            )
            time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser(description="HA stub gRPC server (Mi-Home device stubs)")
    parser.add_argument("--listen", default="127.0.0.1:50055",
                        help="gRPC listen address (localhost only by default)")
    args = parser.parse_args()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    pbg.add_DeviceControlServiceServicer_to_server(HaStubService(), server)
    server.add_insecure_port(args.listen)
    server.start()
    print(f"ha stub gRPC server listening on {args.listen}", flush=True)
    server.wait_for_termination()


if __name__ == "__main__":
    main()
