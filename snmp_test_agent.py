"""A single-request loopback SNMP fixture used by tests and --self-test."""
import socket
import threading
from pyasn1.codec.ber import decoder, encoder
from pysnmp.proto.api import v2c
from ups_snmp import OIDS, read_ups


def query_fixture():
    values = {key: 0 for key in OIDS}
    values.update(source=3, battery_status=2, battery_charge=80, temperature=46,
                  battery_voltage=725, input_freq=500, runtime_min=9)
    values = {OIDS[key]: value for key, value in values.items()}
    errors = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind(('127.0.0.1', 0))
        server.settimeout(5)
        port = server.getsockname()[1]
        def respond():
            try:
                wire, peer = server.recvfrom(65535)
                request, _ = decoder.decode(wire, asn1Spec=v2c.Message())
                response = v2c.apiMessage.get_response(request)
                pdu = v2c.apiMessage.get_pdu(response)
                query = v2c.apiMessage.get_pdu(request)
                v2c.apiPDU.set_varbinds(pdu, [(oid, v2c.Integer(values[str(oid)]))
                    for oid, _ in v2c.apiPDU.get_varbinds(query)])
                server.sendto(encoder.encode(response), peer)
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=respond, daemon=True)
        thread.start()
        try:
            result = read_ups({'snmp_host':'127.0.0.1', 'snmp_community':'test',
                               'snmp_port':port, 'timeout':2})
        finally:
            thread.join(6)
        if errors:
            raise errors[0]
        assert result['battery_voltage'] == 72.5
        assert result['input_freq'] == 50
        assert result['runtime_min'] == 9
        return result
