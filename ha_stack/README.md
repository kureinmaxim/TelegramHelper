# ha_stack — HA Stack (Stubs) for TCP gRPC + Reticulum Testing

A self-contained bundle installed on a VPS by the
[`scripts/install_ha_stack.sh`](../scripts/install_ha_stack.sh) installer.
It simulates an "HA server" with connected Mi-Home devices (**without a real Home
Assistant**) and provides access to them via **two paths**: directly over TCP gRPC
and through the **Reticulum** stack (RNS bridge). Required for e2e tests of the
`UDP_gRPC_COM_Lite` client (commands `device send` and `send`).

## Contents

| File | Role | Source |
|---|---|---|
| `stub_server.py` | gRPC `DeviceControlService`: Mi-Home stubs + `__ping__` / `__list__` | new |
| `ha_adapter_server.py` | same contract → REST HA via tailnet (`:50057`) | vendored from `rns-engine/ha_adapter` |
| `udp_stub.py` | UDP stub device for raw path `/udp_raw` | new |
| `bridge/` | RNS bridge: `/device_control` (proto) + `/udp_raw` (raw) → local gRPC/UDP | vendored from `rns-engine/bridge` |
| `proto/` | `device_control.proto` + generated stubs | vendored from `rns-engine/proto` |
| `rns/config` | RNS bridge config (`TCPServerInterface` 127.0.0.1:50061) | new |
| `requirements.txt` | `rns`, `grpcio`, `protobuf` | new |

> Files in `bridge/` and `proto/` are **copies** from the gRPC engine repo. When the contract changes there, they must be updated here as well (vendoring).

## Ports (All on 127.0.0.1, Not Exposed Externally)

| Port | Service |
|---|---|
| `50055/tcp` | gRPC `DeviceControlService` (stubs) |
| `50056/udp` | UDP stub device (for raw `/udp_raw`) |
| `50061/tcp` | RNS `TCPServerInterface` of the bridge |

## Installation and Access

Installation on VPS and the SSH tunnel diagram are in
[`DEPLOY.md`](../DEPLOY.md) (section "HA Stack (Stubs)"). Update after
`git pull` — in [`POST_DEPLOY.md`](../POST_DEPLOY.md).

`scripts/install_ha_stack.sh` automatically includes soft hang recovery:
swap (if absent), `Restart=always` for the bridge, timer `ha-rns-watchdog`
(port + heartbeat + gRPC), handler timeout in bridge, weekly cron restart of the bridge.
Quick upgrade on a running VPS: `bash scripts/upgrade_ha_rns_watchdog.sh`.
Details — [`RETICULUM_GUIDE.md`](../RETICULUM_GUIDE.md) §10.

Access from your machine — via SSH tunnel (services listen only on localhost):

```bash
ssh -L 50062:127.0.0.1:50061 -L 50055:127.0.0.1:50055 <user>@YOUR_VPS_IP
```

For direct TCP on `YOUR_VPS_IP:50061` — set `listen_ip = 0.0.0.0` (see §6 in `RETICULUM_GUIDE.md`) or use the `--public-rns` flag with [`scripts/install_ha_adapter.sh`](../scripts/install_ha_adapter.sh).

To connect to a real HA: `bash scripts/install_ha_adapter.sh --ha-url <HA_URL> --ha-token <TOKEN> --switch-bridge`.
