"""UDP stub device для raw-пути верхнеуровневого `send`.

Reticulum-мост форвардит сюда сырые байты (--udp-target), мы отвечаем
детерминированным «кадром» Mi-Home устройства. Первый байт payload выбирает
устройство: 0x01=лампочка, 0x02=датчик T/H, 0x03=датчик вибрации.
Слушает только 127.0.0.1 (доступ — через SSH-туннель).

Запуск:  python udp_stub.py --listen-ip 127.0.0.1 --listen-port 50056
"""
import argparse
import socket

_DEVICES = {
    0x01: b"mi_bulb raw ok",
    0x02: b"mi_th_sensor T=235 H=45",
    0x03: b"mi_vibration idle",
}


def main():
    parser = argparse.ArgumentParser(description="HA UDP stub device")
    parser.add_argument("--listen-ip", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=50056)
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.listen_ip, args.listen_port))
    print(f"ha udp stub listening on {args.listen_ip}:{args.listen_port}", flush=True)
    while True:
        data, addr = sock.recvfrom(65535)
        device = data[0] if data else 0
        reply = _DEVICES.get(device, b"unknown device")
        sock.sendto(reply, addr)


if __name__ == "__main__":
    main()
