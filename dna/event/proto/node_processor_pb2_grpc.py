# Generated by the gRPC Python protocol compiler plugin. DO NOT EDIT!
"""Client and server classes corresponding to protobuf-defined services."""
import grpc

import node_processor_pb2 as node__processor__pb2


class NodeProcessorStub(object):
    """Missing associated documentation comment in .proto file."""

    def __init__(self, channel):
        """Constructor.

        Args:
            channel: A grpc.Channel.
        """
        self.Run = channel.unary_stream(
                '/dna.node.proto.NodeProcessor/Run',
                request_serializer=node__processor__pb2.RunNodeProcessRequest.SerializeToString,
                response_deserializer=node__processor__pb2.StatusReport.FromString,
                )
        self.Stop = channel.unary_unary(
                '/dna.node.proto.NodeProcessor/Stop',
                request_serializer=node__processor__pb2.StopNodeProcessRequest.SerializeToString,
                response_deserializer=node__processor__pb2.StatusReport.FromString,
                )


class NodeProcessorServicer(object):
    """Missing associated documentation comment in .proto file."""

    def Run(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')

    def Stop(self, request, context):
        """Missing associated documentation comment in .proto file."""
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details('Method not implemented!')
        raise NotImplementedError('Method not implemented!')


def add_NodeProcessorServicer_to_server(servicer, server):
    rpc_method_handlers = {
            'Run': grpc.unary_stream_rpc_method_handler(
                    servicer.Run,
                    request_deserializer=node__processor__pb2.RunNodeProcessRequest.FromString,
                    response_serializer=node__processor__pb2.StatusReport.SerializeToString,
            ),
            'Stop': grpc.unary_unary_rpc_method_handler(
                    servicer.Stop,
                    request_deserializer=node__processor__pb2.StopNodeProcessRequest.FromString,
                    response_serializer=node__processor__pb2.StatusReport.SerializeToString,
            ),
    }
    generic_handler = grpc.method_handlers_generic_handler(
            'dna.node.proto.NodeProcessor', rpc_method_handlers)
    server.add_generic_rpc_handlers((generic_handler,))


 # This class is part of an EXPERIMENTAL API.
class NodeProcessor(object):
    """Missing associated documentation comment in .proto file."""

    @staticmethod
    def Run(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_stream(request, target, '/dna.node.proto.NodeProcessor/Run',
            node__processor__pb2.RunNodeProcessRequest.SerializeToString,
            node__processor__pb2.StatusReport.FromString,
            options, channel_credentials,
            insecure, call_credentials, compression, wait_for_ready, timeout, metadata)

    @staticmethod
    def Stop(request,
            target,
            options=(),
            channel_credentials=None,
            call_credentials=None,
            insecure=False,
            compression=None,
            wait_for_ready=None,
            timeout=None,
            metadata=None):
        return grpc.experimental.unary_unary(request, target, '/dna.node.proto.NodeProcessor/Stop',
            node__processor__pb2.StopNodeProcessRequest.SerializeToString,
            node__processor__pb2.StatusReport.FromString,
            options, channel_credentials,
            insecure, call_credentials, compression, wait_for_ready, timeout, metadata)
