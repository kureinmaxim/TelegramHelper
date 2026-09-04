"""HA stub gRPC server — имитирует «сервер HA» с подключёнными устройствами
Mi-Home (БЕЗ реальной Home Assistant). Реализует DeviceControlService.SendCommand
детерминированными заглушками устройств:

  mi_bulb       — умная лампочка (LED): WRITE on/off, READ состояние
  mi_th_sensor  — датчик температуры/влажности: READ
  mi_vibration  — датчик вибрации: READ
  __ping__      — health-check (кнопка Ping в ApiRgRPC), как у ha-adapter
  __list__      — JSON-список устройств в read_data (dropdown в ApiRgRPC)

Показания кладутся в существующие поля CommandResponse (message + device_state),
прото НЕ меняется. Слушает только 127.0.0.1 (доступ — через SSH-туннель).

Запуск:  python stub_server.py --listen 127.0.0.1:50055
"""
import argparse
import json
import os
import sys
import time
from concurrent import futures

import grpc

# Делаем пакет proto/ импортируемым (bundle-локально).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from proto import device_control_pb2 as pb            # noqa: E402
from proto import device_control_pb2_grpc as pbg      # noqa: E402

# Спец-id как в ApiRgRPC ha_adapter (GUI Ping / список устройств).
PING_DEVICE_ID = "__ping__"
LIST_DEVICE_ID = "__list__"

# Минимальное in-memory состояние, чтобы WRITE on/off отражался в последующем READ.
_BULB = {"is_on": False, "brightness": 80}


class HaStubService(pbg.DeviceControlServiceServicer):
    """Заглушка DeviceControlService: маршрутизирует по device_id на Mi-Home устройства."""

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
        """Формат как у ha-adapter: {"backend","devices":[{id,entity,state,name,writable}]}."""
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
            message=f"stub: {len(items)} устройств(а)",
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
        """Стрим заглушки (этап 2): 5 периодических событий mi_th_sensor."""
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
                        help="адрес прослушивания gRPC (по умолчанию только localhost)")
    args = parser.parse_args()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    pbg.add_DeviceControlServiceServicer_to_server(HaStubService(), server)
    server.add_insecure_port(args.listen)
    server.start()
    print(f"ha stub gRPC server listening on {args.listen}", flush=True)
    server.wait_for_termination()


if __name__ == "__main__":
    main()
