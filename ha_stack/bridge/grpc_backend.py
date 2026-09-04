"""Bridge backend: CommandRequest -> local gRPC SendCommand -> CommandResponse."""
import grpc
from proto import device_control_pb2 as pb
from proto import device_control_pb2_grpc as pbg


class GrpcCommandBackend:
    def __init__(self, target: str, timeout: float = 10.0):
        self._target = target
        self._timeout = timeout

    def __call__(self, req: pb.CommandRequest) -> pb.CommandResponse:
        try:
            with grpc.insecure_channel(self._target) as channel:
                stub = pbg.DeviceControlServiceStub(channel)
                return stub.SendCommand(req, timeout=self._timeout)
        except grpc.RpcError as exc:
            code = exc.code().name if exc.code() else "UNKNOWN"
            return pb.CommandResponse(status=pb.CommandResponse.ERROR,
                                      message=f"grpc error: {code}")

    def subscribe_events(self, req: pb.EventSubscribeRequest):
        """Стрим (этап 2): открыть канал, вызвать gRPC SubscribeEvents и отдавать
        DeviceEvent по мере прихода. Канал держится открытым на время итерации."""
        channel = grpc.insecure_channel(self._target)
        stub = pbg.DeviceControlServiceStub(channel)
        try:
            for event in stub.SubscribeEvents(req):
                yield event
        finally:
            channel.close()
