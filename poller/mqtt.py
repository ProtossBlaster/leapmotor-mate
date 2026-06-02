"""MQTT telemetry and command service for home automation."""
import json
import logging
import time
from datetime import datetime, timezone
import paho.mqtt.client as mqtt

log = logging.getLogger(__name__)

class MqttService:
    def __init__(self, broker, port, username=None, password=None, topic_prefix="leapmotor", 
                 use_tls=False, tls_insecure=False, discovery_enabled=True):
        self.broker = broker
        self.port = int(port) if port else (8883 if use_tls else 1883)
        self.username = username
        self.password = password
        self.topic_prefix = topic_prefix or "leapmotor"
        self.use_tls = use_tls
        self.tls_insecure = tls_insecure
        self.discovery_enabled = discovery_enabled
        self.client = None
        self.on_command = None
        self._discovery_sent = False
        self._vehicle_image = None

    def connect(self):
        log.info("MQTT: Attempting connection to %s:%d (TLS: %s, Discovery: %s, Prefix: %s)...", 
                 self.broker, self.port, self.use_tls, self.discovery_enabled, self.topic_prefix)
        try:
            # Compatibility with paho-mqtt 2.0+
            try:
                from paho.mqtt.enums import CallbackAPIVersion
                self.client = mqtt.Client(CallbackAPIVersion.VERSION1)
            except ImportError:
                self.client = mqtt.Client()

            if self.username:
                self.client.username_pw_set(self.username, self.password)
            
            if self.use_tls:
                log.info("MQTT: Using TLS")
                self.client.tls_set()
                if self.tls_insecure:
                    log.info("MQTT: TLS insecure mode (ignoring cert errors)")
                    self.client.tls_insecure_set(True)
                
            self.client.on_connect = self._on_connect
            self.client.on_message = self._on_message
            self.client.on_publish = lambda c, u, m: log.debug("MQTT: Data published")
            
            self.client.connect(self.broker, self.port, keepalive=60)
            self.client.loop_start()
            return True
        except Exception as exc:
            log.error("MQTT: connection setup failed: %s", exc)
            self.client = None
            return False

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            log.info("MQTT: Connected successfully to broker")
            # Subscribe to command topics
            topic = f"{self.topic_prefix}/+/command"
            self.client.subscribe(topic)
            # Subscribe to set topics for switches/numbers
            self.client.subscribe(f"{self.topic_prefix}/+/+/set")
            log.info("MQTT: Subscribed to topics under %s/", self.topic_prefix)
            self._discovery_sent = False # Force resend discovery on reconnect
        else:
            errors = {1: "incorrect protocol version", 2: "invalid client identifier", 
                      3: "server unavailable", 4: "bad username or password", 5: "not authorised"}
            log.error("MQTT: connection failed with code %d: %s", rc, errors.get(rc, "unknown"))

    def _on_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            payload = msg.payload.decode()
            log.info("MQTT message received: %s -> %s", topic, payload)
            
            parts = topic.split("/")
            if len(parts) < 3:
                return
                
            vin = parts[1]
            
            if topic.endswith("/command") and self.on_command:
                self.on_command(vin, payload, None)
            elif topic.endswith("/set") and self.on_command:
                # e.g. leapmotor/VIN/ac/set -> ON/OFF
                entity = parts[2]
                self.on_command(vin, entity, payload)
        except Exception as e:
            log.error("Error processing MQTT message: %s", e)

    def disconnect(self):
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            self.client = None

    def publish_status(self, data):
        if not self.client:
            if not self.connect():
                return

        # Check if connected before doing anything
        if not self.client.is_connected():
            log.debug("MQTT: Not yet connected, skipping this cycle")
            return

        log.debug("MQTT: discovery_enabled=%s, _discovery_sent=%s", self.discovery_enabled, self._discovery_sent)
        if self.discovery_enabled and not self._discovery_sent:
            log.info("MQTT: Triggering Home Assistant Discovery...")
            self.publish_discovery(data)
            if self._vehicle_image:
                # Small delay to ensure HA has created the entity before receiving the image
                time.sleep(1)
                self.publish_image(data.vin, self._vehicle_image)
            self._discovery_sent = True

        # 1. Publish full JSON state
        payload = self._build_payload(data)
        base_topic = f"{self.topic_prefix}/{data.vin}"
        log.info("MQTT: Publishing state to %s/state", base_topic)
        self.client.publish(f"{base_topic}/state", json.dumps(payload), retain=True)
        
        # 2. Publish individual sensors for HA
        self._publish_sensors(base_topic, data)

    def _publish_sensors(self, base_topic, data):
        def pub(sub, val, retain=True):
            v = str(val) if val is not None else ""
            if isinstance(val, bool):
                v = "ON" if val else "OFF"
            self.client.publish(f"{base_topic}/{sub}", v, retain=retain)

        pub("soc", data.soc)
        pub("range", data.range_km)
        pub("odometer", data.odometer_km)
        pub("speed", data.speed_kmh)
        pub("gear", data.gear)
        pub("state", data.vehicle_state)
        pub("charging", data.charging_status == 1)
        pub("charge_power", data.charge_power_kw)
        pub("charge_voltage", data.charge_voltage_v)
        pub("charge_current", data.charge_current_a)
        pub("charge_time_remaining", data.remaining_charge_min)
        pub("battery_temp", data.battery_min_temp)
        pub("outside_temp", data.outside_temp)
        pub("inside_temp", data.inside_temp)
        pub("ac_target_temp", data.climate_target_temp)
        pub("locked", data.is_locked)
        pub("climate_on", data.climate_on)
        pub("plug_connected", data.plug_connected)
        
        # Tires
        pub("tire_fl", data.tire_fl_bar)
        pub("tire_fr", data.tire_fr_bar)
        pub("tire_rl", data.tire_rl_bar)
        pub("tire_rr", data.tire_rr_bar)

        pub("any_door_open", data.any_door_open)
        pub("trunk_open", data.trunk_open)
        pub("windows_open", data.windows_open)
        pub("sunshade_open", data.sunshade_open)
        
        # Individual doors
        pub("door_driver", data.door_driver_open)
        pub("door_passenger", data.door_passenger_open)
        pub("door_rear_left", data.door_rear_left_open)
        pub("door_rear_right", data.door_rear_right_open)
        
        # Individual windows
        pub("window_fl", data.window_fl_open)
        pub("window_fr", data.window_fr_open)
        pub("window_rl", data.window_rl_open)
        pub("window_rr", data.window_rr_open)
        pub("last_seen", datetime.now(timezone.utc).isoformat())

        # GPS attributes for device_tracker
        loc = {"latitude": data.latitude, "longitude": data.longitude}
        self.client.publish(f"{base_topic}/location", json.dumps(loc), retain=True)

    def publish_discovery(self, data):
        """Home Assistant MQTT Discovery."""
        vin = data.vin
        prefix = self.topic_prefix
        # Clean device_id for HA
        device_id = f"leapmotor_mate_{vin.lower()}" # Added _mate_ to be unique
        device_name = f"Leapmotor Mate {vin[-6:]}" # Added Mate to be unique
        
        log.info("MQTT: Sending Discovery configs for device %s", device_id)
        
        device_info = {
            "identifiers": [device_id],
            "name": device_name,
            "manufacturer": "Leapmotor",
            "model": "Vehicle",
            "sw_version": "Mate"
        }

        # Discovery prefix (standard is homeassistant)
        disc_p = "homeassistant"

        # ── Sensors ────────────────────────────────────────────────────────
        sensors = [
            {"key": "soc", "name": "Battery", "dc": "battery", "unit": "%"},
            {"key": "range", "name": "Range", "unit": "km", "icon": "mdi:map-marker-distance"},
            {"key": "odometer", "name": "Odometer", "dc": "distance", "unit": "km", "icon": "mdi:counter"},
            {"key": "speed", "name": "Speed", "dc": "speed", "unit": "km/h"},
            {"key": "outside_temp", "name": "Outside Temp", "dc": "temperature", "unit": "°C"},
            {"key": "inside_temp", "name": "Inside Temp", "dc": "temperature", "unit": "°C"},
            {"key": "ac_target_temp", "name": "AC Target", "dc": "temperature", "unit": "°C", "icon": "mdi:thermometer-check"},
            {"key": "battery_temp", "name": "Battery Temp", "dc": "temperature", "unit": "°C", "icon": "mdi:thermometer-low"},
            {"key": "charge_power", "name": "Charge Power", "dc": "power", "unit": "kW"},
            {"key": "charge_voltage", "name": "Charge Voltage", "dc": "voltage", "unit": "V"},
            {"key": "charge_current", "name": "Charge Current", "dc": "current", "unit": "A"},
            {"key": "charge_time_remaining", "name": "Charge Time Remaining", "dc": "duration", "unit": "min", "icon": "mdi:timer-outline"},
            {"key": "tire_fl", "name": "Tire FL", "dc": "pressure", "unit": "bar", "icon": "mdi:tire"},
            {"key": "tire_fr", "name": "Tire FR", "dc": "pressure", "unit": "bar", "icon": "mdi:tire"},
            {"key": "tire_rl", "name": "Tire RL", "dc": "pressure", "unit": "bar", "icon": "mdi:tire"},
            {"key": "tire_rr", "name": "Tire RR", "dc": "pressure", "unit": "bar", "icon": "mdi:tire"},
            {"key": "gear", "name": "Gear", "icon": "mdi:car-shift-pattern"},
            {"key": "state", "name": "State", "icon": "mdi:car-info"},
            {"key": "last_seen", "name": "Last Seen", "dc": "timestamp", "icon": "mdi:clock-outline"},
        ]

        for s in sensors:
            config = {
                "name": s["name"],
                "unique_id": f"{device_id}_{s['key']}",
                "state_topic": f"{prefix}/{vin}/{s['key']}",
                "device": device_info,
            }
            if "unit" in s:
                config["unit_of_measurement"] = s["unit"]
            if "dc" in s:
                config["device_class"] = s["dc"]
            if "icon" in s:
                config["icon"] = s["icon"]
            
            topic = f"{disc_p}/sensor/{device_id}/{s['key']}/config"
            self.client.publish(topic, json.dumps(config), retain=True)

        # ── Binary Sensors ─────────────────────────────────────────────────
        binary_sensors = [
            {"key": "charging", "name": "Charging", "dc": "battery_charging"},
            {"key": "locked", "name": "Locked", "dc": "lock"},
            {"key": "plug_connected", "name": "Plug Connected", "dc": "plug"},
            {"key": "climate_on", "name": "Climate", "dc": "power", "icon": "mdi:air-conditioner"},
            # Doors
            {"key": "door_driver", "name": "Door Driver", "dc": "door", "icon": "mdi:door-open"},
            {"key": "door_passenger", "name": "Door Passenger", "dc": "door", "icon": "mdi:door-open"},
            {"key": "door_rear_left", "name": "Door Rear Left", "dc": "door", "icon": "mdi:door-open"},
            {"key": "door_rear_right", "name": "Door Rear Right", "dc": "door", "icon": "mdi:door-open"},
            {"key": "trunk_open", "name": "Trunk", "dc": "door", "icon": "mdi:car-back"},
            # Windows
            {"key": "window_fl", "name": "Window Front Left", "dc": "window", "icon": "mdi:window-closed-variant"},
            {"key": "window_fr", "name": "Window Front Right", "dc": "window", "icon": "mdi:window-closed-variant"},
            {"key": "window_rl", "name": "Window Rear Left", "dc": "window", "icon": "mdi:window-closed-variant"},
            {"key": "window_rr", "name": "Window Rear Right", "dc": "window", "icon": "mdi:window-closed-variant"},
            {"key": "sunshade_open", "name": "Sunshade", "dc": "window", "icon": "mdi:blinds"},
            # Aggregates
            {"key": "any_door_open", "name": "Any Door", "dc": "door"},
            {"key": "windows_open", "name": "Any Window", "dc": "window"},
        ]

        for bs in binary_sensors:
            config = {
                "name": bs["name"],
                "unique_id": f"{device_id}_{bs['key']}",
                "state_topic": f"{prefix}/{vin}/{bs['key']}",
                "payload_on": "ON", "payload_off": "OFF",
                "device": device_info,
            }
            if "dc" in bs:
                config["device_class"] = bs["dc"]
            if "icon" in bs:
                config["icon"] = bs["icon"]
            topic = f"{disc_p}/binary_sensor/{device_id}/{bs['key']}/config"
            self.client.publish(topic, json.dumps(config), retain=True)

        # ── Buttons (Commands) ─────────────────────────────────────────────
        buttons = [
            {"key": "lock", "name": "Lock", "icon": "mdi:lock"},
            {"key": "unlock", "name": "Unlock", "icon": "mdi:lock-open"},
            {"key": "open_trunk", "name": "Open Trunk", "icon": "mdi:car-back"},
            {"key": "close_trunk", "name": "Close Trunk", "icon": "mdi:car-back"},
            {"key": "find_car", "name": "Find Car", "icon": "mdi:car-search"},
        ]
        
        for b in buttons:
            config = {
                "name": b["name"],
                "unique_id": f"{device_id}_btn_{b['key']}",
                "command_topic": f"{prefix}/{vin}/command",
                "payload_press": b["key"],
                "device": device_info,
                "icon": b["icon"]
            }
            topic = f"{disc_p}/button/{device_id}/{b['key']}/config"
            self.client.publish(topic, json.dumps(config), retain=True)

        # ── Switches (Climate) ─────────────────────────────────────────────
        sw_config = {
            "name": "Climate",
            "unique_id": f"{device_id}_sw_climate",
            "command_topic": f"{prefix}/{vin}/climate/set",
            "state_topic": f"{prefix}/{vin}/climate_on",
            "payload_on": "ON", "payload_off": "OFF",
            "device": device_info,
            "icon": "mdi:air-conditioner"
        }
        self.client.publish(f"{disc_p}/switch/{device_id}/climate/config", json.dumps(sw_config), retain=True)

        # ── Device Tracker ─────────────────────────────────────────────────
        tracker_config = {
            "name": "Location",
            "unique_id": f"{device_id}_location",
            "json_attributes_topic": f"{prefix}/{vin}/location",
            "state_topic": f"{prefix}/{vin}/location",
            "value_template": "{{ 'home' if value_json.latitude else 'not_home' }}",
            "device": device_info,
            "source_type": "gps",
        }
        self.client.publish(f"{disc_p}/device_tracker/{device_id}/config", json.dumps(tracker_config), retain=True)

        # ── Vehicle Image ──────────────────────────────────────────────────
        image_config = {
            "name": "Vehicle Picture",
            "unique_id": f"{device_id}_picture",
            "image_topic": f"{prefix}/{vin}/picture",
            "device": device_info,
        }
        self.client.publish(f"{disc_p}/image/{device_id}/picture/config", json.dumps(image_config), retain=True)

        log.info("MQTT: Discovery configs sent to topic %s/#", disc_p)

    def publish_image(self, vin, image_bytes):
        if not self.client or not self.client.is_connected():
            log.warning("MQTT: Cannot publish image, client not connected")
            return
        if not image_bytes:
            log.warning("MQTT: Image bytes are empty, skipping publish")
            return
        topic = f"{self.topic_prefix}/{vin}/picture"
        log.info("MQTT: Publishing vehicle image to %s (%d bytes)", topic, len(image_bytes))
        self.client.publish(topic, image_bytes, retain=True)

    def set_vehicle_image(self, image_bytes):
        self._vehicle_image = image_bytes
        self._discovery_sent = False # Force resend discovery to include image

    @staticmethod
    def _build_payload(data) -> dict:
        return {
            "vin": data.vin,
            "soc": data.soc,
            "range_km": data.range_km,
            "odometer_km": data.odometer_km,
            "speed_kmh": data.speed_kmh,
            "gear": data.gear,
            "state": data.vehicle_state,
            "charging": data.charging_status == 1,
            "charge_power_kw": data.charge_power_kw,
            "latitude": data.latitude,
            "longitude": data.longitude,
            "outside_temp": data.outside_temp,
            "inside_temp": data.inside_temp,
            "climate_on": data.climate_on,
            "is_locked": data.is_locked,
            "plug_connected": data.plug_connected,
            "any_door_open": data.any_door_open,
            "trunk_open": data.trunk_open,
            "windows_open": data.windows_open,
        }
