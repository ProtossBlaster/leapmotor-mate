"""Optional MQTT bridge — publishes the car to Home Assistant via MQTT Discovery
(native sensors, binary sensors, GPS tracker) and accepts remote commands.

Off unless enabled in Settings with a broker configured. Best-effort: connection
or publish errors are logged, never raised to the poller loop.
"""
import json
import logging
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

import capability_profile

log = logging.getLogger(__name__)

_DISC = "homeassistant"  # HA discovery prefix


class MqttService:
    def __init__(self, broker, port, username=None, password=None, topic_prefix="leapmotor",
                 use_tls=False, tls_insecure=False, discovery_enabled=True, get_setting=None):
        self.broker = broker
        self.port = int(port) if port else (8883 if use_tls else 1883)
        self.username = username
        self.password = password
        self.topic_prefix = topic_prefix or "leapmotor"
        self.use_tls = use_tls
        self.tls_insecure = tls_insecure
        self.discovery_enabled = discovery_enabled
        self.get_setting = get_setting   # db.get_setting — for per-VIN capability gating
        self.client = None
        self.on_command = None          # callback(vin, command_or_entity, value)
        self._discovery_sent = False
        self.last_climate_on = None     # latest polled A/C state, for the "A/C Off" toggle guard

    def connect(self) -> bool:
        log.info("MQTT: connecting to %s:%d (TLS=%s, discovery=%s, prefix=%s)",
                 self.broker, self.port, self.use_tls, self.discovery_enabled, self.topic_prefix)
        try:
            try:  # paho-mqtt 2.0+ requires an explicit callback API version
                from paho.mqtt.enums import CallbackAPIVersion
                self.client = mqtt.Client(CallbackAPIVersion.VERSION1)
            except ImportError:
                self.client = mqtt.Client()
            if self.username:
                self.client.username_pw_set(self.username, self.password)
            if self.use_tls:
                self.client.tls_set()
                if self.tls_insecure:
                    self.client.tls_insecure_set(True)
            self.client.on_connect = self._on_connect
            self.client.on_message = self._on_message
            self.client.connect(self.broker, self.port, keepalive=60)
            self.client.loop_start()
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("MQTT: connection failed: %s", exc)
            self.client = None
            return False

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            log.info("MQTT: connected")
            self.client.subscribe(f"{self.topic_prefix}/+/command")
            self.client.subscribe(f"{self.topic_prefix}/+/+/set")
            self._discovery_sent = False  # resend discovery after a reconnect
        else:
            log.error("MQTT: connect refused (code %d)", rc)

    def _on_message(self, client, userdata, msg):
        try:
            parts = msg.topic.split("/")
            payload = msg.payload.decode()
            if len(parts) < 3 or not self.on_command:
                return
            vin = parts[1]
            if msg.topic.endswith("/command"):
                self.on_command(vin, payload, None)            # button → payload is the command
            elif msg.topic.endswith("/set"):
                self.on_command(vin, parts[2], payload)        # switch → entity + ON/OFF
        except Exception as exc:  # noqa: BLE001
            log.error("MQTT: message error: %s", exc)

    def disconnect(self):
        if self.client:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self.client = None

    # ── Publishing ────────────────────────────────────────────────────────────

    def publish_status(self, data):
        self.last_climate_on = data.climate_on  # track for the "A/C Off" command guard
        if not self.client:
            if not self.connect():
                return
        if not self.client.is_connected():
            return  # still (re)connecting — try again next cycle
        if self.discovery_enabled and not self._discovery_sent:
            self.publish_discovery(data)
            self._discovery_sent = True
        self._publish_sensors(data)

    def _publish_sensors(self, data):
        base = f"{self.topic_prefix}/{data.vin}"

        def pub(sub, val):
            if isinstance(val, bool):
                v = "ON" if val else "OFF"
            else:
                v = "" if val is None else str(val)
            self.client.publish(f"{base}/{sub}", v, retain=True)

        pub("soc", data.soc);                  pub("range", data.range_km)
        pub("odometer", data.odometer_km);     pub("speed", data.speed_kmh)
        pub("gear", data.gear);                pub("state", data.vehicle_state)
        pub("charging", data.charging_status > 0)
        pub("charge_power", data.charge_power_kw)
        pub("charge_voltage", data.charge_voltage_v)
        pub("charge_current", data.charge_current_a)
        pub("charge_time_remaining", data.remaining_charge_min)
        pub("battery_temp", data.battery_min_temp)
        pub("inside_temp", data.inside_temp)
        pub("ac_target_temp", data.climate_target_temp)
        pub("locked", data.is_locked);          pub("climate_on", data.climate_on)
        pub("plug_connected", data.plug_connected)
        pub("tire_fl", data.tire_fl_bar);       pub("tire_fr", data.tire_fr_bar)
        pub("tire_rl", data.tire_rl_bar);       pub("tire_rr", data.tire_rr_bar)
        pub("any_door_open", data.any_door_open); pub("trunk_open", data.trunk_open)
        pub("windows_open", data.windows_open); pub("sunshade_open", data.sunshade_open)
        pub("door_driver", data.door_driver_open);       pub("door_passenger", data.door_passenger_open)
        pub("door_rear_left", data.door_rear_left_open); pub("door_rear_right", data.door_rear_right_open)
        pub("window_fl", data.window_fl_open);  pub("window_fr", data.window_fr_open)
        pub("window_rl", data.window_rl_open);  pub("window_rr", data.window_rr_open)
        pub("seat_heat_driver", data.seat_heat_driver);     pub("seat_heat_passenger", data.seat_heat_passenger)
        pub("seat_vent_driver", data.seat_vent_driver);     pub("seat_vent_passenger", data.seat_vent_passenger)
        pub("steering_heat", data.steering_heat)
        pub("mirror_heat_left", data.mirror_heat_left);     pub("mirror_heat_right", data.mirror_heat_right)
        pub("battery_thermal_request", data.battery_thermal_request)
        pub("last_seen", datetime.now(timezone.utc).isoformat())
        self._publish_evcc(base, data)
        self.client.publish(f"{base}/location",
                            json.dumps({"latitude": data.latitude, "longitude": data.longitude}),
                            retain=True)

    def _publish_evcc(self, base, data):
        """EVCC-friendly boolean mirrors of plug/charging/climate.

        EVCC's Go config parser (strconv.ParseBool) accepts true/false/1/0 but NOT the
        ON/OFF we publish for Home Assistant. These extra `evcc/*` topics let a `type:
        custom` EVCC vehicle read charge status via the documented combined plugged+charging
        pattern (and optional climater). Cheap retained topics — only read if the user wires
        up EVCC. See docs/EVCC.md for a ready-to-paste vehicle config."""
        def b(v):
            return "" if v is None else ("true" if v else "false")
        self.client.publish(f"{base}/evcc/plugged",  b(data.plug_connected),            retain=True)
        self.client.publish(f"{base}/evcc/charging", b((data.charging_status or 0) > 0), retain=True)
        self.client.publish(f"{base}/evcc/climate",  b(data.climate_on),                retain=True)

    def publish_state(self, vin, key, value):
        """Publish a single retained state topic — used for an optimistic update the
        moment a command succeeds, so the HA entity flips without waiting for the
        next full status publish. Same value encoding as _publish_sensors."""
        if not self.client or not self.client.is_connected():
            return
        if isinstance(value, bool):
            v = "ON" if value else "OFF"
        else:
            v = "" if value is None else str(value)
        self.client.publish(f"{self.topic_prefix}/{vin}/{key}", v, retain=True)

    def publish_event(self, vin: str, event_type: str, payload: dict) -> None:
        """Publish a one-shot (non-retained) event to {prefix}/{vin}/event.
        Used for transient signals like battery preconditioning that should trigger
        HA automations but must NOT be replayed to new subscribers as stale state."""
        if not self.client or not self.client.is_connected():
            return
        try:
            body = json.dumps({"type": event_type, **payload}, separators=(",", ":"))
            self.client.publish(f"{self.topic_prefix}/{vin}/event", body, retain=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("MQTT: publish_event failed: %s", exc)

    def publish_discovery(self, data):
        vin = data.vin
        prefix = self.topic_prefix
        # Scope the HA device to the topic prefix, so a second instance on a different
        # prefix (e.g. a test poller alongside the production add-on, same car/VIN)
        # creates a SEPARATE device instead of fighting over the same discovery configs
        # and entities. The default prefix "leapmotor" yields the exact same id as
        # before → existing installs are completely unaffected.
        device_id = f"{prefix}_mate_{vin.lower()}"
        name = f"Leapmotor Mate {vin[-6:]}"
        if prefix != "leapmotor":
            name += f" ({prefix})"
        device = {"identifiers": [device_id], "name": name,
                  "manufacturer": "Leapmotor", "model": "Vehicle", "sw_version": "Mate"}

        def cfg(component, key, conf):
            conf.update({"unique_id": f"{device_id}_{key}", "device": device})
            self.client.publish(f"{_DISC}/{component}/{device_id}/{key}/config",
                                json.dumps(conf), retain=True)

        sensors = [
            ("soc", "Battery", {"dc": "battery", "unit": "%"}),
            ("range", "Range", {"unit": "km", "icon": "mdi:map-marker-distance"}),
            ("odometer", "Odometer", {"dc": "distance", "unit": "km", "icon": "mdi:counter"}),
            ("speed", "Speed", {"dc": "speed", "unit": "km/h"}),
            ("inside_temp", "Inside Temp", {"dc": "temperature", "unit": "°C"}),
            ("ac_target_temp", "AC Target", {"dc": "temperature", "unit": "°C"}),
            ("battery_temp", "Battery Temp", {"dc": "temperature", "unit": "°C"}),
            ("charge_power", "Charge Power", {"dc": "power", "unit": "kW"}),
            ("charge_voltage", "Charge Voltage", {"dc": "voltage", "unit": "V"}),
            ("charge_current", "Charge Current", {"dc": "current", "unit": "A"}),
            ("charge_time_remaining", "Charge Time Remaining", {"dc": "duration", "unit": "min"}),
            ("tire_fl", "Tyre FL", {"dc": "pressure", "unit": "bar", "icon": "mdi:tire"}),
            ("tire_fr", "Tyre FR", {"dc": "pressure", "unit": "bar", "icon": "mdi:tire"}),
            ("tire_rl", "Tyre RL", {"dc": "pressure", "unit": "bar", "icon": "mdi:tire"}),
            ("tire_rr", "Tyre RR", {"dc": "pressure", "unit": "bar", "icon": "mdi:tire"}),
            ("gear", "Gear", {"icon": "mdi:car-shift-pattern"}),
            ("state", "State", {"icon": "mdi:car-info"}),
            ("battery_thermal_request", "Battery Thermal Request", {"icon": "mdi:thermometer-lines"}),
            ("last_seen", "Last Seen", {"dc": "timestamp", "icon": "mdi:clock-outline"}),
        ]
        for key, name, extra in sensors:
            c = {"name": name, "state_topic": f"{prefix}/{vin}/{key}"}
            if "unit" in extra: c["unit_of_measurement"] = extra["unit"]
            if "dc" in extra: c["device_class"] = extra["dc"]
            if "icon" in extra: c["icon"] = extra["icon"]
            cfg("sensor", key, c)

        # Comfort STATE sensors — read-only, shown only where they work on THIS car (the B10
        # reports these even though the matching remote commands are broken). A confirmed-broken
        # one gets its retained config cleared so HA drops it.
        for key, name, feat, icon in [
            # Seats are ROLE-based, NOT physical sides (unlike the doors below): the cloud signals are
            # driver/co-driver (2100/2118) and the matching commands take position=driver|copilot — the
            # car maps the role to the physical side by market, so "Driver/Passenger" is correct on LHD
            # *and* RHD. Reverted from Left/Right (mate#61: on an RHD car "Right" lit the left seat in
            # the app). Entity object_ids kept (no HA churn).
            ("seat_heat_driver",    "Seat Heating Driver",       "seat_heat",     "mdi:car-seat-heater"),
            ("seat_heat_passenger", "Seat Heating Passenger",    "seat_heat",     "mdi:car-seat-heater"),
            ("seat_vent_driver",    "Seat Ventilation Driver",   "seat_vent",     "mdi:car-seat-cooler"),
            ("seat_vent_passenger", "Seat Ventilation Passenger","seat_vent",     "mdi:car-seat-cooler"),
            ("steering_heat",       "Steering Wheel Heat",       "steering_heat", "mdi:steering"),
            ("mirror_heat_left",    "Mirror Heating Left",       "mirror_heat",   "mdi:car-side"),
            ("mirror_heat_right",   "Mirror Heating Right",      "mirror_heat",   "mdi:car-side"),
        ]:
            if capability_profile.is_shown(vin, feat, self.get_setting):
                cfg("sensor", key, {"name": name, "state_topic": f"{prefix}/{vin}/{key}", "icon": icon})
            else:
                self.client.publish(f"{_DISC}/sensor/{device_id}/{key}/config", "", retain=True)

        binaries = [
            ("charging", "Charging", "battery_charging"), ("locked", "Locked", "lock"),
            ("plug_connected", "Plug Connected", "plug"), ("climate_on", "Climate", "power"),
            # Friendly names are physical positions (signals 1277=lbcm/left, 1278=rbcm/right) — the old
            # "Driver/Passenger" labels were wrong on RHD cars. Entity object_ids kept (no HA churn).
            ("door_driver", "Door Front Left", "door"), ("door_passenger", "Door Front Right", "door"),
            ("door_rear_left", "Door Rear Left", "door"), ("door_rear_right", "Door Rear Right", "door"),
            ("trunk_open", "Trunk", "door"),
            ("window_fl", "Window Front Left", "window"), ("window_fr", "Window Front Right", "window"),
            ("window_rl", "Window Rear Left", "window"), ("window_rr", "Window Rear Right", "window"),
            ("sunshade_open", "Sunshade", "window"),
            ("any_door_open", "Any Door", "door"), ("windows_open", "Any Window", "window"),
        ]
        for key, name, dc in binaries:
            conf = {"name": name, "state_topic": f"{prefix}/{vin}/{key}",
                    "payload_on": "ON", "payload_off": "OFF", "device_class": dc}
            if key == "locked":
                # HA's `lock` device_class is inverted (on = unlocked, off = locked).
                # We publish ON = locked, so swap the payloads → a locked car shows
                # "Locked" (not "Unlocked"). The published topic value is unchanged.
                conf["payload_on"], conf["payload_off"] = "OFF", "ON"
            cfg("binary_sensor", key, conf)

        # Single "Door Lock" TOGGLE (HA `lock` platform): one control that shows the
        # locked state AND locks/unlocks on tap — so it fits as a single Home-Assistant
        # button (e.g. a phone front-screen shortcut), GitHub #37. Reuses the `locked`
        # state topic (ON = locked); LOCK/UNLOCK on `door_lock/set` route to the existing
        # lock/unlock commands. The separate momentary Lock/Unlock buttons stay for anyone
        # already using them.
        cfg("lock", "door_lock", {
            "name": "Door Lock",
            "state_topic": f"{prefix}/{vin}/locked",
            "command_topic": f"{prefix}/{vin}/door_lock/set",
            "payload_lock": "LOCK", "payload_unlock": "UNLOCK",
            "state_locked": "ON", "state_unlocked": "OFF",
        })
        # Same control as a SWITCH (ON = locked): launcher/dashboard widgets (e.g. the
        # Samsung HA widget, GitHub #38) force a fixed action on `lock` entities, but
        # they CAN toggle a switch — so this is the one-button lock/unlock toggle.
        cfg("switch", "lock_toggle", {
            "name": "Door Lock Toggle",
            "state_topic": f"{prefix}/{vin}/locked",
            "command_topic": f"{prefix}/{vin}/lock_toggle/set",
            "payload_on": "ON", "payload_off": "OFF",
            "state_on": "ON", "state_off": "OFF",
            "icon": "mdi:lock",
        })

        # Single "Trunk" TOGGLE (HA `switch` platform): one control that shows the trunk
        # open/closed state AND opens/closes it on tap — the trunk analog of the Door Lock
        # Toggle above (#71). ON = open. Reuses the `trunk_open` state topic; ON/OFF on
        # `trunk/set` route to the existing open_trunk/close_trunk commands. A plain toggle
        # (like the lock) reads more clearly than a cover's open/close arrows. The separate
        # Open/Close Trunk buttons below stay for existing automations.
        cfg("switch", "trunk", {
            "name": "Trunk",
            "state_topic": f"{prefix}/{vin}/trunk_open",
            "command_topic": f"{prefix}/{vin}/trunk/set",
            "payload_on": "ON", "payload_off": "OFF",
            "state_on": "ON", "state_off": "OFF",
            "icon": "mdi:car-back",
        })

        for key, name, icon in [
            # NB: no Lock/Unlock buttons here — superseded by the Door Lock lock entity
            # + Door Lock Toggle switch above (their retained configs are cleared below);
            # the raw "lock"/"unlock" command payloads STAY accepted for existing automations.
            ("open_trunk", "Open Trunk", "mdi:car-back"), ("close_trunk", "Close Trunk", "mdi:car-back"),
            ("find_car", "Find Car", "mdi:car-search"),
            ("unlock_charger", "Unlock Charge Cable", "mdi:ev-plug-type2"),
            # Climate is exposed as momentary buttons (not a switch): the API has no
            # single on/off toggle, only distinct mode commands + ac_switch to deactivate.
            ("climate_cool", "Quick Cool", "mdi:snowflake"),
            ("climate_heat", "Quick Heat", "mdi:fire"),
            ("climate_vent", "Quick Ventilation", "mdi:fan"),
            ("climate_defrost", "Defrost", "mdi:car-defrost-front"),
            ("climate_off", "A/C Off", "mdi:snowflake-off"),
            # Comfort — model-aware (gated by capability; B10 now supported via kerniger payloads).
            ("steering_heat_on", "Steering Heat On", "mdi:steering"),
            ("steering_heat_off", "Steering Heat Off", "mdi:steering"),
            ("mirror_heat_on", "Mirror Heat On", "mdi:mirror-rectangle"),
            ("mirror_heat_off", "Mirror Heat Off", "mdi:mirror-rectangle"),
            ("seat_heat_driver_on", "Driver Seat Heat On", "mdi:car-seat-heater"),
            ("seat_heat_driver_off", "Driver Seat Heat Off", "mdi:car-seat-heater"),
            ("seat_heat_passenger_on", "Passenger Seat Heat On", "mdi:car-seat-heater"),
            ("seat_heat_passenger_off", "Passenger Seat Heat Off", "mdi:car-seat-heater"),
            ("seat_vent_driver_on", "Driver Seat Vent On", "mdi:car-seat-cooler"),
            ("seat_vent_driver_off", "Driver Seat Vent Off", "mdi:car-seat-cooler"),
            ("seat_vent_passenger_on", "Passenger Seat Vent On", "mdi:car-seat-cooler"),
            ("seat_vent_passenger_off", "Passenger Seat Vent Off", "mdi:car-seat-cooler"),
        ]:
            # Model-aware: hide command buttons confirmed broken on THIS car (e.g. A/C Off on
            # the B10). Clearing the retained config makes HA drop a button that was published
            # before it was classified as broken. Unknown/working commands are always shown.
            if capability_profile.command_shown(vin, key, self.get_setting):
                cfg("button", key, {"name": name, "command_topic": f"{prefix}/{vin}/command",
                                    "payload_press": key, "icon": icon})
            else:
                self.client.publish(f"{_DISC}/button/{device_id}/{key}/config", "", retain=True)

        # The old single "Climate" switch is deprecated in favour of the buttons above
        # (a plain switch can't model cool/heat/defrost and its OFF was a no-op). Clear
        # its retained discovery config so it disappears from existing installs. The
        # read-only "Climate" binary_sensor (climate_on) still shows the live A/C state.
        self.client.publish(f"{_DISC}/switch/{device_id}/climate/config", "", retain=True)
        # Likewise the separate Lock/Unlock momentary buttons: fully redundant since the
        # Door Lock entity (state + both actions) and the Door Lock Toggle switch exist.
        # Clearing the retained configs makes HA drop them on existing installs too.
        self.client.publish(f"{_DISC}/button/{device_id}/lock/config", "", retain=True)
        self.client.publish(f"{_DISC}/button/{device_id}/unlock/config", "", retain=True)
        cfg("device_tracker", "location", {"name": "Location",
                                           "json_attributes_topic": f"{prefix}/{vin}/location",
                                           "state_topic": f"{prefix}/{vin}/location",
                                           "value_template": "{{ 'home' if value_json.latitude else 'not_home' }}",
                                           "source_type": "gps"})
        log.info("MQTT: Home Assistant discovery published for %s", device_id)
