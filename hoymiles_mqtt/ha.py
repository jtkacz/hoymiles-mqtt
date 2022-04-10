import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

from hoymiles_modbus.datatypes import PlantData

PLATFORM_SENSOR = 'sensor'
PLATFORM_BINARY_SENSOR = 'binary_sensor'

DEVICE_CLASS_VOLTAGE = 'voltage'
DEVICE_CLASS_CURRENT = 'current'
DEVICE_CLASS_FREQUENCY = 'frequency'
DEVICE_CLASS_POWER = 'power'
DEVICE_CLASS_ENERGY = 'energy'
DEVICE_CLASS_TEMPERATURE = 'temperature'

STATE_CLASS_MEASUREMENT = 'measurement'
STATE_CLASS_TOTAL = 'total'

UNIT_VOLTS = 'V'
UNIT_AMPERES = 'A'
UNIT_HERTZ = 'Hz'
UNIT_CELSIUS = '°C'
UNIT_WATS = 'W'
UNIT_WATS_PER_HOUR = 'Wh'

ZERO = 0


@dataclass
class EntityDescription:
    platform: str = PLATFORM_SENSOR
    device_class: Optional[str] = None
    unit: Optional[str] = None
    state_class: Optional[str] = None
    ignored_value: Optional[Any] = None


MicroinverterEntities = {
    'port_number': EntityDescription(),
    'pv_voltage': EntityDescription(
        device_class=DEVICE_CLASS_VOLTAGE, unit=UNIT_VOLTS, state_class=STATE_CLASS_MEASUREMENT
    ),
    'pv_current': EntityDescription(
        device_class=DEVICE_CLASS_CURRENT, unit=UNIT_AMPERES, state_class=STATE_CLASS_MEASUREMENT
    ),
    'grid_voltage': EntityDescription(
        device_class=DEVICE_CLASS_VOLTAGE, unit=UNIT_VOLTS, state_class=STATE_CLASS_MEASUREMENT
    ),
    'grid_frequency': EntityDescription(
        device_class=DEVICE_CLASS_FREQUENCY, unit=UNIT_HERTZ, state_class=STATE_CLASS_MEASUREMENT
    ),
    'pv_power': EntityDescription(device_class=DEVICE_CLASS_POWER, unit=UNIT_WATS, state_class=STATE_CLASS_MEASUREMENT),
    'today_production': EntityDescription(
        device_class=DEVICE_CLASS_ENERGY, unit=UNIT_WATS_PER_HOUR, state_class=STATE_CLASS_TOTAL
    ),
    'total_production': EntityDescription(
        device_class=DEVICE_CLASS_ENERGY, unit=UNIT_WATS_PER_HOUR, state_class=STATE_CLASS_TOTAL
    ),
    'temperature': EntityDescription(
        device_class=DEVICE_CLASS_TEMPERATURE, unit=UNIT_CELSIUS, state_class=STATE_CLASS_MEASUREMENT
    ),
    'operating_status': EntityDescription(),
    'alarm_code': EntityDescription(),
    'alarm_count': EntityDescription(),
    'link_status': EntityDescription(),
}

DtuEntities = {
    'pv_power': EntityDescription(device_class=DEVICE_CLASS_POWER, unit=UNIT_WATS, state_class=STATE_CLASS_MEASUREMENT),
    'today_production': EntityDescription(
        device_class=DEVICE_CLASS_ENERGY, unit=UNIT_WATS_PER_HOUR, state_class=STATE_CLASS_TOTAL, ignored_value=ZERO
    ),
    'total_production': EntityDescription(
        device_class=DEVICE_CLASS_ENERGY, unit=UNIT_WATS_PER_HOUR, state_class=STATE_CLASS_TOTAL, ignored_value=ZERO
    ),
    'alarm_flag': EntityDescription(platform=PLATFORM_BINARY_SENSOR),
}


class HassMqtt:
    def __init__(self, hide_microinverters: bool = False, post_process: bool = True):
        self._hide_microinverters: bool = hide_microinverters
        self._state_topics: Dict = {}
        self._config_topics: Dict = {}
        self._post_process: bool = post_process
        self._production_today_cache: Dict[str, int] = {}
        self._production_total_cache: Dict[str, int] = {}

    def _get_config_topic(self, platform: str, device_serial: str, entity_name):
        return f"homeassistant/{platform}/{device_serial}/{entity_name}/config"

    def _get_state_topic(self, device_serial: str):
        return f"homeassistant/hoymiles_mqtt/{device_serial}/state"

    def _get_config_payloads(
        self, device_name: str, device_serial_number, entity_definitions: Dict[str, EntityDescription]
    ) -> Iterable[Tuple[str, str]]:
        for entity_name, entity_definition in entity_definitions.items():
            config_payload = {
                "device": {
                    "name": f"{device_name}_{device_serial_number}",
                    "identifiers": [f"hoymiles_mqtt_{device_serial_number}"],
                    "manufacturer": "Hoymiles",
                },
                "name": f"{device_name}_{device_serial_number}_{entity_name}",
                "unique_id": f"hoymiles_mqtt_{device_serial_number}_{entity_name}",
                "state_topic": self._get_state_topic(device_serial_number),
                "value_template": "{{ value_json.%s }}" % entity_name,
            }
            if entity_definition.device_class:
                config_payload['device_class'] = entity_definition.device_class
            if entity_definition.unit:
                config_payload['unit_of_measurement'] = entity_definition.unit
            if entity_definition.state_class:
                config_payload['state_class'] = entity_definition.state_class
            config_topic = self._get_config_topic(entity_definition.platform, device_serial_number, entity_name)
            yield config_topic, json.dumps(config_payload)

    def clear_production_today(self):
        self._production_today_cache = {}

    def get_configs(self, plant_data: PlantData):
        for topic, payload in self._get_config_payloads('DTU', plant_data.dtu, DtuEntities):
            yield topic, payload
        if not self._hide_microinverters:
            for microinverter_data in plant_data.microinverter_data:
                for topic, payload in self._get_config_payloads(
                    'Microinverter', microinverter_data.serial_number, MicroinverterEntities
                ):
                    yield topic, payload

    def _get_state(self, device_serial: str, entity_definitions: Dict[str, EntityDescription], entity_data):
        values = {}
        for entity_name, description in entity_definitions.items():
            value = getattr(entity_data, entity_name)
            if description.ignored_value is not None and value == description.ignored_value:
                continue
            values[entity_name] = str(value)
        payload = json.dumps(values)
        state_topic = self._get_state_topic(device_serial)
        return state_topic, payload

    def _update_cache(self, plant_data: PlantData):
        for microinverter in plant_data.microinverter_data:
            if microinverter.serial_number not in self._production_today_cache:
                self._production_today_cache[microinverter.serial_number] = ZERO
            if microinverter.serial_number not in self._production_total_cache:
                self._production_total_cache[microinverter.serial_number] = ZERO
            if microinverter.link_status:
                self._production_today_cache[microinverter.serial_number] = microinverter.today_production
                self._production_total_cache[microinverter.serial_number] = microinverter.total_production

    def _process_plant_data(self, plant_data: PlantData):
        self._update_cache(plant_data)
        production_today = ZERO
        production_total = ZERO
        if self._production_today_cache and ZERO not in self._production_today_cache.values():
            production_today = sum(self._production_today_cache.values())
        if self._production_total_cache and ZERO not in self._production_total_cache.values():
            production_total = sum(self._production_total_cache.values())
        plant_data.today_production = production_today
        plant_data.total_production = production_total

    def get_states(self, plant_data: PlantData):
        if self._post_process:
            self._process_plant_data(plant_data)
        yield self._get_state(plant_data.dtu, DtuEntities, plant_data)
        if not self._hide_microinverters:
            for microinverter_data in plant_data.microinverter_data:
                yield self._get_state(microinverter_data.serial_number, MicroinverterEntities, microinverter_data)
