"""HA adapter gRPC server — реальный Home Assistant вместо заглушки.

Реализует тот же DeviceControlService (device_control.proto), что и
stub_server.py, но транслирует команды в REST API Home Assistant:

  READ                → GET  /api/states/<entity_id>
  WRITE set_led_state → POST /api/services/<domain>/turn_on|turn_off
  INIT                → GET  /api/  (проверка живости HA)

Деплой: рядом со stub_server.py на VPS (/opt/TelegramHelper/ha_stack/),
HA живёт на NAS и доступен через tailnet — участок VPS→NAS шифрует WireGuard.

Конфигурация — переменные окружения (systemd EnvironmentFile, права 600):
  HA_URL        например http://100.64.0.20:8123  (tailnet-адрес NAS)
  HA_TOKEN      long-lived access token из профиля HA
  HA_DEVICE_MAP путь к JSON: {"имя_в_GUI": "switch.entity_id", ...}
                (опционально; device_id с точкой трактуется как entity_id)

Запуск:  python ha_adapter_server.py --listen 127.0.0.1:50055
"""
import argparse
import json
import os
import sys
import time
from concurrent import futures
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import grpc

# Пакет proto/ — bundle-локально (как у stub_server.py).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from proto import device_control_pb2 as pb
    from proto import device_control_pb2_grpc as pbg
except ImportError:  # запуск из каталога ha_adapter/ в репо ApiRgRPC
    import device_control_pb2 as pb
    import device_control_pb2_grpc as pbg

POLL_INTERVAL_S = 2.0  # период опроса состояний для SubscribeEvents
LIST_DEVICE_ID = "__list__"  # спец-запрос списка устройств (по пути SendCommand)
PING_DEVICE_ID = "__ping__"  # health-check: живость HA без привязки к устройству


# ---------------------------------------------------------------------------
# REST-клиент Home Assistant (stdlib, без зависимостей)
# ---------------------------------------------------------------------------
class HaClient:
    def __init__(self, base_url: str, token: str, timeout: float = 6.0):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    def _request(self, method: str, path: str, payload: dict | None = None):
        url = self.base_url + path
        data = json.dumps(payload).encode() if payload is not None else None
        req = Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        })
        with urlopen(req, timeout=self._timeout) as resp:
            return json.loads(resp.read() or b"null")

    def alive(self) -> bool:
        try:
            return "message" in (self._request("GET", "/api/") or {})
        except (HTTPError, URLError, OSError):
            return False

    def state(self, entity_id: str) -> dict:
        return self._request("GET", "/api/states/" + quote(entity_id))

    def states_all(self) -> list:
        """Все состояния одним запросом (для сборки списка без N round-trips)."""
        return self._request("GET", "/api/states") or []

    def call_service(self, domain: str, service: str, entity_id: str,
                     data: dict | None = None) -> None:
        payload = {"entity_id": entity_id}
        if data:
            payload.update(data)
        self._request("POST", f"/api/services/{quote(domain)}/{quote(service)}", payload)

    def render_template(self, template: str) -> str:
        """POST /api/template — HA рендерит Jinja на сервере (есть area_name и т.п.).
        Возвращает сырой текст (не JSON), поэтому отдельно от _request."""
        url = self.base_url + "/api/template"
        data = json.dumps({"template": template}).encode()
        req = Request(url, data=data, method="POST", headers={
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        })
        with urlopen(req, timeout=self._timeout) as resp:
            return (resp.read() or b"").decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# Маппинг device_id (имя в GUI) → entity_id HA
# ---------------------------------------------------------------------------
def load_device_map() -> dict[str, str]:
    path = os.environ.get("HA_DEVICE_MAP", "")
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def resolve_entity(device_id: str, dev_map: dict[str, str]) -> str | None:
    if device_id in dev_map:
        return dev_map[device_id]
    if "." in device_id:  # уже entity_id (switch.xxx / sensor.yyy)
        return device_id
    return None


# ---------------------------------------------------------------------------
# Автообнаружение устройств из HA (фаза 3): один запрос /api/template отдаёт
# все entity нужных доменов с областью (area_name) — вместо ручного маппинга.
# ---------------------------------------------------------------------------
DISCOVER_DOMAINS = ("light", "switch", "input_boolean", "cover", "fan",
                    "climate", "lock", "scene", "script", "automation",
                    "sensor", "binary_sensor")
WRITABLE_DOMAINS = ("switch", "light", "input_boolean", "cover", "fan", "lock",
                    "scene", "script", "automation", "climate")
# Домены, за которыми следит SubscribeEvents (live в ApiHA).
WATCH_DOMAINS = WRITABLE_DOMAINS + ("binary_sensor", "sensor")
# Диагностические сенсоры Zigbee — не показываем (шум): связь, батарея-жкв и т.п.
_SKIP_SENSOR_SUFFIX = ("_linkquality", "_rssi", "_lqi", "_last_seen",
                       "_device_temperature", "_power_outage_count",
                       "_available", "_update_available", "_restart", "_identify")

# Управляемые домены берём целиком; sensor/binary_sensor — только с «полезным»
# device_class (иначе список тонет в диагностике: linkquality, battery, voltage,
# power, date/time и т.п. — их у Zigbee-устройств десятки).
_SENSOR_CLASSES = ("temperature", "humidity", "motion", "occupancy", "presence",
                   "door", "window", "opening", "moisture", "smoke", "gas",
                   "carbon_monoxide", "illuminance", "power", "energy")
MAX_DISCOVER = 150  # предохранитель на размер ответа по RNS-линку

# JSONL: по одному JSON-объекту на строку (устойчиво к пропускам, без запятых).
# HA-идиоматично: s.domain и state_attr() (доступ s.attributes.x падает в
# песочнице HA на entity без атрибута и рушит весь рендер).
_DISCOVER_TEMPLATE = (
    "{% for s in states %}"
    "{% set dc = state_attr(s.entity_id, 'device_class') or '' %}"
    "{% if s.domain in ['light','switch','input_boolean','cover','fan','climate','lock',"
    "'scene','script','automation']"
    " or (s.domain in ['sensor','binary_sensor'] and dc in "
    "['temperature','humidity','motion','occupancy','presence','door','window',"
    "'opening','moisture','smoke','gas','carbon_monoxide','illuminance','power','energy']) %}"
    '{"e": {{ s.entity_id|tojson }}, "s": {{ s.state|tojson }}, '
    '"n": {{ (state_attr(s.entity_id, "friendly_name") or s.entity_id)|tojson }}, '
    '"a": {{ (area_name(s.entity_id) or "")|tojson }}, '
    '"u": {{ (state_attr(s.entity_id, "unit_of_measurement") or "")|tojson }}, '
    '"dc": {{ dc|tojson }}, '
    '"m": {{ (state_attr(s.entity_id, "model") or "")|tojson }}, '
    '"mf": {{ (state_attr(s.entity_id, "manufacturer") or "")|tojson }}}\n'
    "{% endif %}"
    "{% endfor %}"
)


def discover_entities(ha: "HaClient") -> list[dict]:
    """Список entity из HA (управляемые + полезные сенсоры, с областью). [] при ошибке."""
    try:
        text = ha.render_template(_DISCOVER_TEMPLATE)
    except HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        print(f"[autodiscover] /api/template HTTP {exc.code}: {body}", file=sys.stderr, flush=True)
        return []
    except (URLError, OSError, ValueError) as exc:
        print(f"[autodiscover] /api/template failed: {exc}", file=sys.stderr, flush=True)
        return []
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        e = o.get("e", "")
        d = e.split(".", 1)[0]
        if d in ("sensor", "binary_sensor") and e.endswith(_SKIP_SENSOR_SUFFIX):
            continue
        out.append(o)
        if len(out) >= MAX_DISCOVER:
            break
    if not out:
        print(f"[autodiscover] /api/template ok, но 0 устройств (ответ {len(text)} символов)",
              file=sys.stderr, flush=True)
    else:
        print(f"[autodiscover] обнаружено {len(out)} устройств", file=sys.stderr, flush=True)
    return out


# ---------------------------------------------------------------------------
# Сборка ответов в терминах device_control.proto
# ---------------------------------------------------------------------------
def node_from_state(entity_id: str, st: dict, block) -> "pb.DeviceNode":
    """Перевести состояние HA-entity в DeviceNode (без изменения прото)."""
    attrs = st.get("attributes") or {}
    state = str(st.get("state", "unknown"))
    domain = entity_id.split(".", 1)[0]
    node = pb.DeviceNode(id=entity_id, block=block)

    if domain in ("switch", "light", "input_boolean"):
        node.type = pb.DeviceType.DEVICETYPE_LED
        node.led_state.is_on = state == "on"
    elif domain == "sensor" and attrs.get("device_class") == "temperature":
        node.type = pb.DeviceType.DEVICETYPE_TEMPERATURE
        try:
            node.temperature_state.temperature_celsius = float(state)
        except ValueError:
            pass
    else:
        node.type = pb.DeviceType.DEVICETYPE_UNKNOWN
        node.generic_state_data = state.encode()

    # Полезные атрибуты — в parameters (мощность розетки, влажность и т.п.)
    for key in ("friendly_name", "device_class", "unit_of_measurement",
                "power", "current_power_w", "load_power", "temperature",
                "humidity", "battery"):
        if key in attrs and attrs[key] is not None:
            node.parameters[key] = str(attrs[key])
    node.parameters["ha_state"] = state
    return node


def human_message(entity_id: str, st: dict) -> str:
    attrs = st.get("attributes") or {}
    name = attrs.get("friendly_name") or entity_id
    unit = attrs.get("unit_of_measurement") or ""
    extra = ""
    for key in ("current_power_w", "load_power", "power"):
        if attrs.get(key) not in (None, ""):
            extra = f", power={attrs[key]}W"
            break
    return f"{name}: {st.get('state')}{unit}{extra}"


def err(status, message: str) -> "pb.CommandResponse":
    return pb.CommandResponse(status=status, message=message)


def enrich_from_state(item: dict, st: dict | None) -> dict:
    """Добавить brightness / climate setpoint и т.п. для UI ApiHA."""
    if not st or not isinstance(st, dict):
        return item
    attrs = st.get("attributes") or {}
    entity = item.get("entity") or item.get("id") or ""
    domain = entity.split(".", 1)[0]
    if domain == "light":
        b = attrs.get("brightness")
        if b is not None:
            try:
                bi = int(b)
                item["brightness"] = bi
                item["brightness_pct"] = max(1, min(100, round(bi * 100 / 255)))
            except (TypeError, ValueError):
                pass
    elif domain == "climate":
        item["current_temperature"] = attrs.get("current_temperature")
        item["temperature"] = attrs.get("temperature")
        item["hvac_modes"] = attrs.get("hvac_modes") or []
        item["min_temp"] = attrs.get("min_temp")
        item["max_temp"] = attrs.get("max_temp")
        item["writable"] = True
    elif domain == "lock":
        item["writable"] = True
    # Модель/бренд для каталога внешнего вида в ApiHA (Z2M / device attrs).
    for src, dst in (("model", "model"), ("manufacturer", "manufacturer"),
                     ("device_id", "device_id"), ("power", "power"),
                     ("current_power_w", "current_power_w"),
                     ("load_power", "load_power"), ("battery", "battery")):
        if attrs.get(src) not in (None, "") and not item.get(dst):
            item[dst] = attrs[src]
    return item


def apply_ha_write(ha: HaClient, entity: str, *, led_on=None, attrs: dict | None = None):
    """WRITE: light attrs / climate setpoint / lock / scene / automation.trigger / turn_on|off."""
    domain = entity.split(".", 1)[0]
    attrs = dict(attrs or {})

    if domain == "scene":
        ha.call_service("scene", "turn_on", entity)
        return
    # ApiHA «Запуск» → trigger (не turn_on/off — это enable/disable automation).
    if domain == "automation":
        ha.call_service("automation", "trigger", entity)
        return
    if domain == "climate":
        if "temperature" in attrs or "target_temp_high" in attrs or "target_temp_low" in attrs:
            data = {k: attrs[k] for k in (
                "temperature", "target_temp_high", "target_temp_low", "hvac_mode"
            ) if k in attrs}
            ha.call_service("climate", "set_temperature", entity, data=data)
            return
        if "hvac_mode" in attrs:
            ha.call_service("climate", "set_hvac_mode", entity,
                            data={"hvac_mode": attrs["hvac_mode"]})
            return
        if led_on is False or str(attrs.get("state", "")).lower() in ("off", "false", "0"):
            ha.call_service("climate", "turn_off", entity)
            return
        if led_on is True or attrs:
            attrs.pop("state", None)
            ha.call_service("climate", "turn_on", entity, data=attrs or None)
            return
        return
    if domain == "lock":
        if led_on is True or str(attrs.get("state", "")).lower() in ("unlock", "unlocked", "on"):
            ha.call_service("lock", "unlock", entity)
        else:
            ha.call_service("lock", "lock", entity)
        return
    if led_on is not None and not attrs:
        svc = "turn_on" if led_on else "turn_off"
        if domain == "script":
            ha.call_service("script", svc, entity)
        else:
            ha.call_service(domain, svc, entity)
        return
    if str(attrs.get("state", "")).lower() in ("off", "false", "0"):
        ha.call_service(domain, "turn_off", entity)
    else:
        attrs.pop("state", None)
        ha.call_service(domain, "turn_on", entity, data=attrs or None)


# ---------------------------------------------------------------------------
# Сервис
# ---------------------------------------------------------------------------
class HaAdapterService(pbg.DeviceControlServiceServicer):
    def __init__(self, ha: HaClient, dev_map: dict[str, str]):
        self._ha = ha
        self._map = dev_map
        self._entity_to_gui = {v: k for k, v in dev_map.items()}

    # -- список устройств (маркер __list__ по пути SendCommand) --------------
    def _list_payload(self):
        items = []
        aliased = set(self._map.values())
        # bulk-состояния одним запросом — состояния алиасов без N round-trips
        try:
            by_id = {s.get("entity_id"): s for s in self._ha.states_all()
                     if isinstance(s, dict)}
        except (HTTPError, URLError, OSError):
            by_id = {}
        # 1) курируемые алиасы из HA_DEVICE_MAP (kitchen_plug, mi_bulb, …)
        for gui, entity in sorted(self._map.items()):
            st = by_id.get(entity)
            if st is None:
                state, name = "unreachable", entity
            else:
                state = str(st.get("state"))
                name = (st.get("attributes") or {}).get("friendly_name") or entity
            domain = entity.split(".", 1)[0]
            item = {
                "id": gui, "entity": entity, "state": state, "name": name,
                "area": "", "alias": True, "writable": domain in WRITABLE_DOMAINS,
            }
            items.append(enrich_from_state(item, st))
        # 2) автообнаружение из HA (кроме entity, уже покрытых алиасом)
        if os.environ.get("HA_AUTODISCOVER", "1") != "0":
            for o in discover_entities(self._ha):
                e = o.get("e", "")
                if not e or e in aliased:
                    continue
                domain = e.split(".", 1)[0]
                item = {
                    "id": e, "entity": e, "state": o.get("s", ""),
                    "name": o.get("n") or e, "area": o.get("a") or "",
                    "unit": o.get("u", ""), "device_class": o.get("dc", ""),
                    "model": o.get("m") or "",
                    "manufacturer": o.get("mf") or "",
                    "writable": domain in WRITABLE_DOMAINS,
                }
                items.append(enrich_from_state(item, by_id.get(e)))
        return json.dumps({"backend": "real-ha", "devices": items}).encode()

    # -- SendCommand --------------------------------------------------------
    def SendCommand(self, request, context):
        if request.device_id == LIST_DEVICE_ID:
            payload = self._list_payload()
            n = len(self._map)
            return pb.CommandResponse(
                status=pb.CommandResponse.SUCCESS,
                message=f"real HA: {n} устройств(а)",
                read_data=payload,
            )
        if request.device_id == PING_DEVICE_ID:
            ok = self._ha.alive()
            return pb.CommandResponse(
                status=pb.CommandResponse.SUCCESS if ok else pb.CommandResponse.ERROR,
                message="HA API alive" if ok else "HA API unreachable",
            )
        entity = resolve_entity(request.device_id, self._map)
        if not entity:
            known = ", ".join(sorted(self._map)) or "—"
            return err(pb.CommandResponse.ERROR,
                       f"unknown device: {request.device_id} (known: {known})")
        try:
            # WRITE с generic_payload = JSON: light attrs / climate setpoint / …
            if request.command_code == pb.CommandCode.WRITE and \
                    request.HasField("generic_payload"):
                try:
                    attrs = json.loads(request.generic_payload.decode("utf-8") or "{}")
                except (ValueError, UnicodeDecodeError):
                    attrs = {}
                if not isinstance(attrs, dict):
                    attrs = {}
                apply_ha_write(self._ha, entity, attrs=attrs)
                time.sleep(0.3)
                st = self._ha.state(entity)
                return pb.CommandResponse(
                    status=pb.CommandResponse.SUCCESS,
                    message=human_message(entity, st),
                    device_state=node_from_state(entity, st, request.block_type),
                )
            if request.command_code == pb.CommandCode.WRITE and \
                    request.HasField("set_led_state"):
                apply_ha_write(self._ha, entity, led_on=bool(request.set_led_state.state))
                time.sleep(0.3)  # дать ZHA применить состояние
                st = self._ha.state(entity)
                return pb.CommandResponse(
                    status=pb.CommandResponse.SUCCESS,
                    message=human_message(entity, st),
                    device_state=node_from_state(entity, st, request.block_type),
                )
            if request.command_code in (pb.CommandCode.READ,
                                        pb.CommandCode.COMMAND_UNKNOWN):
                st = self._ha.state(entity)
                return pb.CommandResponse(
                    status=pb.CommandResponse.SUCCESS,
                    message=human_message(entity, st),
                    device_state=node_from_state(entity, st, request.block_type),
                )
            if request.command_code == pb.CommandCode.INIT:
                ok = self._ha.alive()
                return pb.CommandResponse(
                    status=pb.CommandResponse.SUCCESS if ok else pb.CommandResponse.ERROR,
                    message="HA API alive" if ok else "HA API unreachable",
                )
            return err(pb.CommandResponse.NOT_SUPPORTED,
                       f"command {request.command_code} not supported")
        except HTTPError as e:
            return err(pb.CommandResponse.ERROR, f"HA HTTP {e.code}: {e.reason}")
        except (URLError, OSError) as e:
            return err(pb.CommandResponse.TIMEOUT, f"HA unreachable: {e}")

    # -- GetDevices ---------------------------------------------------------
    def GetDevices(self, request, context):
        resp = pb.GetDeviceResponse()
        for gui_name, entity in sorted(self._map.items()):
            try:
                st = self._ha.state(entity)
                node = node_from_state(entity, st, request.block_type)
            except (HTTPError, URLError, OSError):
                node = pb.DeviceNode(id=entity, block=request.block_type)
                node.parameters["ha_state"] = "unreachable"
            node.parameters["gui_name"] = gui_name
            resp.devices.append(node)
        return resp

    # -- GetBlockStatus -----------------------------------------------------
    def GetBlockStatus(self, request, context):
        ok = self._ha.alive()
        return pb.GetBlockStatusResponse(
            block_type=request.block_type,
            connected=ok,
            status_message="HA API alive" if ok else "HA API unreachable",
            parameters={"devices_mapped": str(len(self._map))},
        )

    # -- SubscribeEvents: опрос HA, событие при изменении (live ApiHA) -------
    def SubscribeEvents(self, request, context):
        last: dict[str, str] = {}
        while context.is_active():
            try:
                states = self._ha.states_all()
            except (HTTPError, URLError, OSError):
                time.sleep(POLL_INTERVAL_S)
                continue
            for st in states:
                if not isinstance(st, dict):
                    continue
                entity = st.get("entity_id") or ""
                domain = entity.split(".", 1)[0]
                if domain not in WATCH_DOMAINS:
                    continue
                attrs = st.get("attributes") or {}
                state = str(st.get("state"))
                # fingerprint: для climate/light ловим смену setpoint/яркости
                fp = state
                if domain == "climate":
                    fp += f"|{attrs.get('temperature')}|{attrs.get('current_temperature')}"
                elif domain == "light":
                    fp += f"|{attrs.get('brightness')}"
                if last.get(entity) in (None, fp):
                    last[entity] = fp
                    continue
                last[entity] = fp
                gui = self._entity_to_gui.get(entity, entity)
                bright_pct = None
                if attrs.get("brightness") is not None:
                    try:
                        bright_pct = max(1, min(100, round(int(attrs["brightness"]) * 100 / 255)))
                    except (TypeError, ValueError):
                        bright_pct = None
                payload = json.dumps({
                    "id": gui,
                    "entity": entity,
                    "state": state,
                    "name": attrs.get("friendly_name") or entity,
                    "brightness": attrs.get("brightness"),
                    "brightness_pct": bright_pct,
                    "current_temperature": attrs.get("current_temperature"),
                    "temperature": attrs.get("temperature"),
                    "device_class": attrs.get("device_class") or "",
                    "unit": attrs.get("unit_of_measurement") or "",
                }, ensure_ascii=False).encode("utf-8")
                yield pb.DeviceEvent(
                    block_type=request.block_type,
                    device_id=gui,
                    event_type=pb.EventType.EVENT_UNKNOWN,
                    timestamp_unix_ms=int(time.time() * 1000),
                    payload=payload,
                )
            time.sleep(POLL_INTERVAL_S)


def main():
    parser = argparse.ArgumentParser(description="HA adapter gRPC server (real Home Assistant)")
    parser.add_argument("--listen", default="127.0.0.1:50055",
                        help="адрес прослушивания gRPC (по умолчанию только localhost)")
    args = parser.parse_args()

    ha_url = os.environ.get("HA_URL", "")
    ha_token = os.environ.get("HA_TOKEN", "")
    if not ha_url or not ha_token:
        print("FATAL: HA_URL и HA_TOKEN обязательны (env)", file=sys.stderr)
        sys.exit(2)

    ha = HaClient(ha_url, ha_token)
    dev_map = load_device_map()
    if not ha.alive():
        print(f"WARNING: HA API {ha_url} сейчас недоступен — стартую, буду ретраить",
              flush=True)

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    pbg.add_DeviceControlServiceServicer_to_server(HaAdapterService(ha, dev_map), server)
    server.add_insecure_port(args.listen)
    server.start()
    print(f"HA adapter gRPC server listening on {args.listen} → {ha_url} "
          f"(devices mapped: {len(dev_map)})", flush=True)
    server.wait_for_termination()


if __name__ == "__main__":
    main()
