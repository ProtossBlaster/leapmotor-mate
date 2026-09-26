"""Normalize observed read responses without inferring missing vehicle data."""
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType

from .errors import ValidationError
from .models import require_aware
from .sensor_catalog import BASELINE_SIGNALS


def _freeze(value, depth=0):
    if depth > 20:
        raise ValidationError("Vehicle response nesting exceeded")
    if isinstance(value, Mapping):
        if any(not isinstance(k, str) for k in value):
            raise ValidationError("Expected string response keys")
        return MappingProxyType({k: _freeze(v, depth + 1) for k, v in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(v, depth + 1) for v in value)
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValidationError("Invalid vehicle response value")


def _time(value):
    # Only observed epoch milliseconds or explicitly timezone-aware ISO dates.
    # Other encodings remain available in raw data, never replaced by receipt time.
    try:
        if isinstance(value,str) and value.isascii() and value.isdecimal() and len(value)<=16:
            value=int(value)
        if type(value) in (int, float) and math.isfinite(value) and value > 0:
            return datetime.fromtimestamp(value / 1000, timezone.utc)
        if isinstance(value, str) and value:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                return parsed
    except (ValueError, OverflowError, OSError):
        pass
    return None


def _data(response, expected_vin):
    if not isinstance(expected_vin, str) or not expected_vin:
        raise ValidationError("Expected vehicle identity")
    if not isinstance(response, Mapping):
        raise ValidationError("Expected vehicle response")
    # Accept a successful API envelope or an already unwrapped data object.
    if 'data' in response:
        codes = [response[k] for k in ('code', 'result') if k in response]
        if not codes or any(type(c) not in (str, int) or str(c) != '0' for c in codes):
            raise ValidationError("Vehicle API response rejected")
        response = response['data']
    if not isinstance(response, Mapping) or response.get('vin') != expected_vin:
        raise ValidationError("Vehicle response identity mismatch")
    return response


def _number(value, low, high):
    if type(value) not in (str, int, float):
        return None
    try:
        n = float(value)
    except (ValueError, OverflowError):
        return None
    return n if math.isfinite(n) and low <= n <= high else None


@dataclass(frozen=True, slots=True)
class TelemetrySnapshot:
    vin: str = field(repr=False)
    received_at: datetime
    observed_at: datetime | None
    raw: Mapping = field(repr=False)
    values: Mapping
    unknown_signals: Mapping = field(repr=False)
    source: str = 'signalMap'
    cloud_collected_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ConfigurationSnapshot:
    vin: str = field(repr=False)
    received_at: datetime
    observed_at: datetime | None
    group_times: Mapping
    raw: Mapping = field(repr=False)
    charge: Mapping
    source: str = 'commonConfig'


# Numeric signal IDs are strings. These labels are limited to observed contracts.
SIGNALS = MappingProxyType({
    **BASELINE_SIGNALS,
    '1204': ('soc_percent', 0, 100),
    '100003': ('precise_soc_percent', 0, 100),
    '2646': ('front_left_pressure_kpa', 0, 1000),
    '2653': ('front_right_pressure_kpa', 0, 1000),
    '2660': ('rear_left_pressure_kpa', 0, 1000),
    '2667': ('rear_right_pressure_kpa', 0, 1000),
})


def normalize_telemetry(response, *, expected_vin, received_at):
    require_aware(received_at)
    data = _data(response, expected_vin)
    signals = data.get('signalMap')
    if not isinstance(signals, Mapping):
        raise ValidationError("Expected signalMap; legacy shapes require an explicit adapter")
    raw = _freeze(data)
    values = {label: _number(signals.get(code), low, high)
              for code, (label, low, high) in SIGNALS.items()}
    unknown = {k: v for k, v in raw['signalMap'].items() if k not in SIGNALS}
    observed = _time(signals.get('1'))
    return TelemetrySnapshot(expected_vin, received_at, observed, raw,
                             _freeze(values), _freeze(unknown),cloud_collected_at=_time(data.get('collectTime')))


def normalize_configuration(response, *, expected_vin, received_at):
    require_aware(received_at)
    data = _data(response, expected_vin)
    groups = data.get('config')
    if not isinstance(groups, Mapping):
        raise ValidationError("Expected configuration groups")
    raw = _freeze(data)
    group = groups.get('3', {})
    if not isinstance(group, Mapping):
        raise ValidationError("Invalid charge configuration")
    flag = group.get('isEnable')
    enabled = None
    if type(flag) in (str, int) and str(flag) in ('0', '1'):
        enabled = str(flag) == '1'
    cycles = group.get('cycles')
    days = None
    if isinstance(cycles, str):
        parts = cycles.split(',')
        if len(parts) == 7 and all(v in ('0', '1') for v in parts):
            # Order observed in the app builder; retain the original raw string.
            days = tuple(v == '1' for v in parts)
    times = {k: _time(v.get('updateTime')) if isinstance(v, Mapping) else None
             for k, v in groups.items()}
    charge = {'enabled': enabled, 'limit_percent': _number(group.get('percent'), 0, 100),
              'begin_time_raw': group.get('beginTime'), 'end_time_raw': group.get('endTime'),
              'cycles_sunday_first': days, 'timezone': None,
              'recharge_raw': group.get('recharge'), 'circulation_raw': group.get('circulation')}
    return ConfigurationSnapshot(expected_vin, received_at, _time(data.get('collectTime')),
                                 MappingProxyType(times), raw, _freeze(charge))
