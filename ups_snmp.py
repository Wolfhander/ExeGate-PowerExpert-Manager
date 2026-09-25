"""RFC 1628 monitoring through the PySNMP 7 asyncio API (worker threads only)."""
import asyncio
import math

try:
    from pysnmp.hlapi.v3arch.asyncio import (
        SnmpEngine, CommunityData, UdpTransportTarget, ContextData,
        ObjectType, ObjectIdentity, get_cmd,
    )
    SNMP_AVAILABLE = True
except ImportError:
    SNMP_AVAILABLE = False

# RFC 1628: runtime is minutes, battery voltage is decivolts, frequency is decihertz.
OIDS = {
    "input_voltage": "1.3.6.1.2.1.33.1.3.3.1.3.1",
    "output_voltage": "1.3.6.1.2.1.33.1.4.4.1.2.1",
    "output_load_pct": "1.3.6.1.2.1.33.1.4.4.1.5.1",
    "output_watts": "1.3.6.1.2.1.33.1.4.4.1.4.1",
    "battery_charge": "1.3.6.1.2.1.33.1.2.4.0",
    "runtime_min": "1.3.6.1.2.1.33.1.2.3.0",
    "temperature": "1.3.6.1.2.1.33.1.2.7.0",
    "battery_voltage": "1.3.6.1.2.1.33.1.2.5.0",
    "input_freq": "1.3.6.1.2.1.33.1.3.3.1.2.1",
    "source": "1.3.6.1.2.1.33.1.4.1.0",
    "battery_status": "1.3.6.1.2.1.33.1.2.1.0",
}

async def _get(host, community, oids, port, timeout):
    engine = SnmpEngine()
    try:
        target = await UdpTransportTarget.create((host, port), timeout=timeout, retries=0)
        error, status, index, bindings = await get_cmd(
            engine, CommunityData(community, mpModel=1), target, ContextData(),
            *(ObjectType(ObjectIdentity(oid)) for oid in oids), lookupMib=False,
        )
        if error:
            raise RuntimeError(str(error))
        if status:
            raise RuntimeError(f"{status.prettyPrint()} (index {index})")
        return {str(oid): value.prettyPrint() for oid, value in bindings}
    finally:
        engine.close_dispatcher()


def snmp_get(host, community, oids, port=161, timeout=3.0):
    if not SNMP_AVAILABLE:
        raise RuntimeError("Установите pysnmp>=7.1,<8")
    async def bounded():
        return await asyncio.wait_for(_get(host, community, oids, int(port), timeout), timeout + 2)
    return asyncio.run(bounded())


def decode_ups(values):
    """Reject incomplete critical data rather than inventing a mains-restored state."""
    result = {}
    for key, oid in OIDS.items():
        try:
            value = float(values[oid])
            if not math.isfinite(value):
                continue
            result[key] = value
        except (ValueError, TypeError, KeyError):
            continue
    if not {"source", "battery_status", "battery_charge"} <= result.keys():
        raise ValueError("Неполный ответ UPS MIB: нет состояния питания/батареи")
    source = result.pop("source")
    batt = result.pop("battery_status")
    if source not in (3, 4, 5, 6, 7) or batt not in (2, 3, 4):
        raise ValueError("ИБП сообщает неизвестное состояние питания/батареи")
    if not 0 <= result["battery_charge"] <= 100:
        raise ValueError("Некорректный заряд батареи")
    result["utility_fail"] = source == 5
    result["bypass_active"] = source == 4
    result["battery_low"] = batt in (3, 4)
    result["battery_voltage"] = result.get("battery_voltage", 0) / 10
    result["battery_voltage_raw"] = result["battery_voltage"]
    result["input_freq"] = result.get("input_freq", 0) / 10
    # Unsupported temperature must not look like a new measured 0 °C.
    result.setdefault("temperature", float("nan"))
    for key in ("battery_charge", "output_load_pct", "output_watts", "runtime_min"):
        if key in result:
            result[key] = int(result[key])
    return result


def read_ups(connection):
    values = snmp_get(connection["snmp_host"], connection["snmp_community"], list(OIDS.values()),
                      connection.get("snmp_port", 161), float(connection.get("timeout", 3)))
    return decode_ups(values)
