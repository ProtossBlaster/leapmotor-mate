"""Optional MQTT bridge — publishes the car to Home Assistant via MQTT Discovery
(native sensors, binary sensors, GPS tracker) and accepts remote commands.

Off unless enabled in Settings with a broker configured. Best-effort: connection
or publish errors are logged, never raised to the poller loop.
"""
import json
import logging
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

import capability_profile

log = logging.getLogger(__name__)

_DISC = "homeassistant"  # HA discovery prefix

# Sub-topic each install publishes itself on, so two of them sharing a prefix can find out. It costs
# one small unretained message per poll and answers a question nothing else could: the device id in
# discovery is built from the prefix and the VIN, so two installs on the same prefix watching the
# same car are INDISTINGUISHABLE to Home Assistant — one device, and both of them subscribed to the
# same wildcard command topic. Nobody notices, because the states they publish agree.
_BEACON = "mate_instance"

# Value template for a topic whose payload is legitimately EMPTY sometimes. Without it Home
# Assistant hands "" to the device_class parser and logs an error on every poll; with it the entity
# simply reads `unknown`, which is the honest answer. Only an empty string is falsy in Jinja, so a
# real "0" still goes through.
_EMPTY_NONE = "{{ value if value else none }}"


class MqttService:
    def __init__(self, broker, port, username=None, password=None, topic_prefix="leapmotor",
                 use_tls=False, tls_insecure=False, discovery_enabled=True, get_setting=None,
                 abilities=None, car_type="", instance_id="", is_beta=False):
        self.broker = broker
        self.port = int(port) if port else (8883 if use_tls else 1883)
        self.username = username
        self.password = password
        self.topic_prefix = topic_prefix or "leapmotor"
        self.use_tls = use_tls
        self.tls_insecure = tls_insecure
        self.discovery_enabled = discovery_enabled
        self.get_setting = get_setting   # db.get_setting — for per-VIN capability gating
        self.abilities = abilities       # car's declared ability codes → ability-gated command buttons (#142)
        self.car_type = car_type         # car model → hide model-absent entities (e.g. heated seats on T03, #144)
        self.client = None
        self.on_command = None          # callback(vin, command_or_entity, value)
        self.on_collision = None        # callback(other_id, other_is_beta, vin) — see _BEACON below
        # Who WE are on this broker, and what the live bridge was built with. `instance_id` is the
        # install's own `mate_device_id` (already generated at first run), so no new identity is
        # invented for this.
        self.instance_id = instance_id or ""
        self.is_beta = bool(is_beta)
        self.config_sig = None
        self._own_vins = set()          # VINs WE publish for — a command for any other is refused
        self._discovery_sent = False
        # #144 — temperature topic keys this car has never reported, and what we last told HA about
        # them. `None` (not an empty set) for the second one: "nothing published yet" has to be
        # distinguishable from "published, and nothing was absent", or the first discovery is skipped.
        self.absent_temps: set = set()
        self._temps_published = None
        self.last_climate_on = None     # latest polled A/C state, for the "A/C Off" toggle guard
        # V2L live state: the idle baseline current (I0) frozen at session start, the running session
        # energy, and the previous-poll idle current that seeds I0. Mirrors the net-power math that
        # db_reader.get_v2l_sessions rebuilds from the positions log for history.
        self._v2l_active = False
        self._v2l_i0_a = 0.0
        self._v2l_prev_current = 0.0
        self._v2l_energy_wh = 0.0
        self._v2l_last_mono = None

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
            self.client.subscribe(f"{self.topic_prefix}/+/{_BEACON}")
            self._discovery_sent = False  # resend discovery after a reconnect
        else:
            log.error("MQTT: connect refused (code %d)", rc)

    def _on_message(self, client, userdata, msg):
        try:
            parts = msg.topic.split("/")
            payload = msg.payload.decode()
            if len(parts) < 3:
                return
            vin = parts[1]
            if msg.topic.endswith(f"/{_BEACON}"):
                self._handle_beacon(vin, payload)
                return
            if not self.on_command:
                return
            # The command topics are wildcards — `<prefix>/+/command` takes ANY vin — and the vin was
            # handed straight to the cloud API. Two installs sharing a prefix therefore each executed
            # the other's commands, and against a car that is not theirs. Only ours, only now.
            if self._own_vins and vin not in self._own_vins:
                log.warning("MQTT: ignoring a command for %s — not this instance's car. Another Mate "
                            "is probably publishing on the same topic prefix (%s).", vin, self.topic_prefix)
                return
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

    def publish_status(self, data, absent_temps=None):
        """One poll's state. `absent_temps` is the set of temperature TOPIC keys this car has never
        reported (#144) — measured by the caller from the log, so the bridge stays free of DB access.

        None is "no new measurement", NOT "nothing is absent": the previous answer stands, and before
        the first one that is the empty set, so a caller that never measures shows every entity. An
        absent answer must never delete an entity. → [[signal-absent-is-not-signal-zero]]"""
        self.last_climate_on = data.climate_on  # track for the "A/C Off" command guard
        if absent_temps is not None:
            self.absent_temps = set(absent_temps)
        if not self.client:
            if not self.connect():
                return
        if not self.client.is_connected():
            return  # still (re)connecting — try again next cycle
        if self.discovery_enabled and not self._discovery_sent:
            self.publish_discovery(data)
            self._discovery_sent = True
        elif self.discovery_enabled and self.absent_temps != self._temps_published:
            # ⚠️ Discovery runs ONCE per connection, and this answer CHANGES: it needs 50 polls of
            # evidence before it says anything, so the first pass after a fresh install always shows
            # all three and the removal is due ~25 minutes later. Without this the entity would only
            # go away at the next reconnect — and a sensor that starts working would never come back.
            self._publish_temp_entities(data)
        self._publish_sensors(data)

    # The three temperature entities and their HA config, shared by discovery and the re-check above
    # so one list cannot describe them differently in two places.
    _TEMP_ENTITIES = (("inside_temp", "Inside Temp"),
                      ("ac_target_temp", "AC Target"),
                      ("battery_temp", "Battery Temp"))

    def _publish_temp_entities(self, data):
        """(Re)publish or clear the three temperature configs for this car. A retained EMPTY config
        is how HA is told to drop an entity — the same mechanism the seat entities use (v2.6.1)."""
        vin = data.vin
        device_id = f"{self.topic_prefix}_mate_{vin.lower()}"
        for key, name in self._TEMP_ENTITIES:
            topic = f"{_DISC}/sensor/{device_id}/{key}/config"
            if key in self.absent_temps:
                self.client.publish(topic, "", retain=True)
                continue
            self.client.publish(topic, json.dumps(
                {"name": name, "state_topic": f"{self.topic_prefix}/{vin}/{key}",
                 "unit_of_measurement": "°C", "device_class": "temperature",
                 "unique_id": f"{device_id}_{key}", "device": self._device(vin)}), retain=True)
        self._temps_published = set(self.absent_temps)

    def _v2l_live(self, data):
        """Live V2L numbers for MQTT: NET discharge power (gross minus the idle baseline I0 frozen at
        session start, so the car's own overhead isn't billed to the external load) and the energy
        accumulated this session. Returns (active, power_w, energy_wh). The net-power gate also means
        a latched 47==2 with the load off reads 0 W and stops accruing energy."""
        now = time.monotonic()
        if data.ac_port_mode == 2:
            if not self._v2l_active:                       # session just started → freeze the baseline
                self._v2l_i0_a = max(0.0, self._v2l_prev_current)
                self._v2l_energy_wh = 0.0
                self._v2l_last_mono = now
                self._v2l_active = True
            net_w = max(0.0, data.charge_current_a - self._v2l_i0_a) * data.charge_voltage_v
            if self._v2l_last_mono is not None:
                dt_h = (now - self._v2l_last_mono) / 3600.0
                if 0 < dt_h <= 5 / 60:                     # ignore sleep/offline gaps > 5 min
                    self._v2l_energy_wh += net_w * dt_h
            self._v2l_last_mono = now
            return True, round(net_w), round(self._v2l_energy_wh, 1)
        # not in V2L → remember this idle current to seed the next session's baseline; reset live values
        self._v2l_active = False
        self._v2l_prev_current = data.charge_current_a
        return False, 0, 0.0

    def _handle_beacon(self, vin, payload):
        """Another Mate just announced itself on our prefix — or we heard our own echo.

        The beacon is deliberately NOT retained: a message arriving at all means the sender is
        publishing right now. That is what makes this reliable without a last-will, a timestamp or
        any clock agreement between two machines — a retained one would keep accusing an instance
        that was uninstalled months ago."""
        if not payload or not self.instance_id:
            return
        try:
            other = json.loads(payload)
        except (ValueError, TypeError):
            return
        # Anything at all can publish to this topic, and the consequence of believing it is that a
        # BetaTester install renames its own prefix. So: a real object, carrying a real id, that is
        # not ours. An empty `{}` used to read as "somebody else with no name" and was enough to
        # move it.
        if not isinstance(other, dict):
            return
        other_id = other.get("id")
        if not isinstance(other_id, str) or not other_id or other_id == self.instance_id:
            return                               # nameless, or our own beacon coming back to us
        log.warning("MQTT: another Mate is publishing on prefix '%s' (car %s). Same prefix means one "
                    "device in Home Assistant and EVERY command executed twice.",
                    self.topic_prefix, vin)
        if self.on_collision:
            self.on_collision(other_id, bool(other.get("beta")), vin)

    def _publish_sensors(self, data):
        base = f"{self.topic_prefix}/{data.vin}"
        self._own_vins.add(data.vin)
        # Say who we are, every cycle, unretained. Cheap (one small message per poll) and it is the
        # only way an install can find out it is sharing an identity with another one.
        if self.instance_id:
            self.client.publish(f"{base}/{_BEACON}",
                                json.dumps({"id": self.instance_id, "beta": self.is_beta}),
                                retain=False)

        def pub(sub, val):
            if isinstance(val, bool):
                v = "ON" if val else "OFF"
            else:
                v = "" if val is None else str(val)
            self.client.publish(f"{base}/{sub}", v, retain=True)

        pub("soc", data.soc);                  pub("range", data.range_km)
        pub("odometer", data.odometer_km);     pub("speed", data.speed_kmh)
        pub("gear", data.gear);                pub("state", data.vehicle_state)
        # The car is powered up — signal 1258 (ON3), the same one the Ready automation triggers on.
        # `state` above cannot stand in for it: it only turns to "driving" once a gear is engaged or
        # the car moves, which is after the moment an automation wants (#220 @Torbynator — by then
        # the car is refusing the very commands the automation would send). A door opening is the
        # other end of the same problem: it fires early, and it fires without a drive following.
        pub("ready", data.ready)
        pub("charging", data.charging_status > 0)
        pub("charge_power", data.charge_power_kw)
        pub("charge_voltage", data.charge_voltage_v)
        pub("charge_current", data.charge_current_a)
        pub("charge_time_remaining", data.remaining_charge_min)
        if data.charge_limit_percent is not None:   # absent on some models (T03) → keep last retained value
            pub("charge_limit", data.charge_limit_percent)
        v2l_on, v2l_w, v2l_wh = self._v2l_live(data)
        pub("v2l_active", v2l_on)
        pub("v2l_power", v2l_w)
        pub("v2l_energy_session", v2l_wh)
        pub("battery_temp", data.battery_min_temp)
        pub("inside_temp", data.inside_temp)
        pub("ac_target_temp", data.climate_target_temp)
        pub("locked", data.is_locked);          pub("climate_on", data.climate_on)
        pub("fan_level", data.fan_level or None)              # acAirVolume 1-7 (empty when no data)
        pub("recirculation", data.recirculation)             # binary: recirc on / fresh off
        pub("climate_mode", data.climate_mode_label or None) # auto/cool/heat/vent
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
        pub("last_seen", datetime.now(timezone.utc).isoformat())
        # The CAR's own clock on this frame, and how far behind it has fallen (#178 @riri19).
        # `last_seen` above is when MATE wrote the row — the POLL clock — so it stays a few seconds
        # old forever: Mate polls on a timer and the cloud always answers. When the car can't reach
        # the cloud, the cloud re-serves the last frame it holds, and that fresh `last_seen` then
        # sits on top of half-hour-old contents. These two say how old the CONTENT is.
        #
        # Deliberately UNGATED, unlike the Overview's "· data 33m old" tail. That gate (car driving
        # or charging, and the data behind the polling) exists so a sleeping car doesn't paint
        # "data 8h old" on the panel every morning — a decision about a screen. An automation wants
        # the raw number and its OWN conditions, and the phone is the only co-signer that can't
        # freeze along with the cloud (Mate's own speed/gear/charging go out frozen here too).
        #
        # Both empty when the car doesn't report its clock: timestamp_ms is
        # `int(sig.get("sts") or sig.get("1") or 0)` and 0 would hand HA 1 January 1970 with an age
        # of fifty-six years, every poll, forever. The discovery configs map empty → none.
        if data.timestamp_ms:
            pub("frame_ts", datetime.fromtimestamp(data.timestamp_ms / 1000, timezone.utc).isoformat())
            # Clamped at 0: a car clock running AHEAD of the host isn't negative staleness — it's the
            # same drift recorder.py measured at −48 s in the wild, with the sign the other way.
            pub("data_age", max(0, int(time.time() - data.timestamp_ms / 1000)))
        else:
            pub("frame_ts", None)
            pub("data_age", None)
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

    def _device(self, vin):
        """The HA device this car's entities hang off. Extracted so the temperature re-check can
        rebuild the identical descriptor — a second copy that drifted would create a second device.

        Scoped to the topic prefix, so a second instance on a different prefix (e.g. a test poller
        alongside the production add-on, same car/VIN) creates a SEPARATE device instead of fighting
        over the same discovery configs and entities. The default prefix "leapmotor" yields the exact
        same id as before → existing installs are completely unaffected."""
        device_id = f"{self.topic_prefix}_mate_{vin.lower()}"
        name = f"Leapmotor Mate {vin[-6:]}"
        if self.topic_prefix != "leapmotor":
            name += f" ({self.topic_prefix})"
        return {"identifiers": [device_id], "name": name,
                "manufacturer": "Leapmotor", "model": "Vehicle", "sw_version": "Mate"}

    def publish_discovery(self, data):
        vin = data.vin
        prefix = self.topic_prefix
        device_id = f"{prefix}_mate_{vin.lower()}"
        device = self._device(vin)

        def cfg(component, key, conf):
            conf.update({"unique_id": f"{device_id}_{key}", "device": device})
            self.client.publish(f"{_DISC}/{component}/{device_id}/{key}/config",
                                json.dumps(conf), retain=True)

        sensors = [
            ("soc", "Battery", {"dc": "battery", "unit": "%"}),
            ("range", "Range", {"unit": "km", "icon": "mdi:map-marker-distance"}),
            ("odometer", "Odometer", {"dc": "distance", "unit": "km", "icon": "mdi:counter"}),
            ("speed", "Speed", {"dc": "speed", "unit": "km/h"}),
            ("charge_power", "Charge Power", {"dc": "power", "unit": "kW"}),
            ("charge_voltage", "Charge Voltage", {"dc": "voltage", "unit": "V"}),
            ("charge_current", "Charge Current", {"dc": "current", "unit": "A"}),
            ("charge_time_remaining", "Charge Time Remaining", {"dc": "duration", "unit": "min"}),
            ("v2l_power", "V2L Power", {"dc": "power", "unit": "W", "icon": "mdi:home-lightning-bolt"}),
            ("v2l_energy_session", "V2L Session Energy", {"unit": "Wh", "icon": "mdi:lightning-bolt"}),
            ("tire_fl", "Tyre FL", {"dc": "pressure", "unit": "bar", "icon": "mdi:tire"}),
            ("tire_fr", "Tyre FR", {"dc": "pressure", "unit": "bar", "icon": "mdi:tire"}),
            ("tire_rl", "Tyre RL", {"dc": "pressure", "unit": "bar", "icon": "mdi:tire"}),
            ("tire_rr", "Tyre RR", {"dc": "pressure", "unit": "bar", "icon": "mdi:tire"}),
            ("gear", "Gear", {"icon": "mdi:car-shift-pattern"}),
            ("state", "State", {"icon": "mdi:car-info"}),
            ("last_seen", "Last Seen", {"dc": "timestamp", "icon": "mdi:clock-outline"}),
            # Last Seen is when MATE last wrote; Data Timestamp is when the CAR last spoke, and Data
            # Age is the distance between them (#178). `_EMPTY_NONE` is what keeps a car that never
            # reports its clock from logging a parse error on every single poll — see _publish_sensors.
            ("frame_ts", "Data Timestamp", {"dc": "timestamp", "icon": "mdi:car-clock", "tpl": _EMPTY_NONE}),
            ("data_age", "Data Age", {"dc": "duration", "unit": "s",
                                      "icon": "mdi:timer-sand", "tpl": _EMPTY_NONE}),
            ("climate_mode", "Climate Mode", {"icon": "mdi:air-conditioner"}),
        ]
        for key, name, extra in sensors:
            c = {"name": name, "state_topic": f"{prefix}/{vin}/{key}"}
            if "unit" in extra: c["unit_of_measurement"] = extra["unit"]
            if "dc" in extra: c["device_class"] = extra["dc"]
            if "icon" in extra: c["icon"] = extra["icon"]
            if "tpl" in extra: c["value_template"] = extra["tpl"]
            cfg("sensor", key, c)

        # The three temperatures live in their own pass because they are the only entities gated on a
        # MEASUREMENT that keeps changing (#144): a car that has never once sent one does not get the
        # entity, and publish_status re-runs this the moment that answer moves.
        self._publish_temp_entities(data)

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
            if capability_profile.is_shown(vin, feat, self.get_setting, car_type=self.car_type):
                cfg("sensor", key, {"name": name, "state_topic": f"{prefix}/{vin}/{key}", "icon": icon})
            else:
                self.client.publish(f"{_DISC}/sensor/{device_id}/{key}/config", "", retain=True)

        binaries = [
            ("charging", "Charging", "battery_charging"), ("locked", "Locked", "lock"),
            ("plug_connected", "Plug Connected", "plug"), ("climate_on", "Climate", "power"),
            ("v2l_active", "V2L Active", "power"),
            # `running` rather than `power`: HA reads it as "is this thing going", which is what a
            # powered-up car is, and it gives the automation an edge to trigger on (#220).
            ("ready", "Ready", "running"),
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

        # Fan level (signal 1941) as a writable HA NUMBER (1-7) and air recirculation (signal 1943)
        # as a writable HA SWITCH — both validated on-car 2026-06-20. state_topic mirrors the live
        # value; the /set command_topic routes to set_fan_level / set_recirc (poller dispatch). A
        # single entity each = state + control (no separate read-only sensor).
        cfg("number", "fan_level", {
            "name": "Fan Level",
            "state_topic": f"{prefix}/{vin}/fan_level",
            "command_topic": f"{prefix}/{vin}/fan_level/set",
            "min": 1, "max": 7, "step": 1, "icon": "mdi:fan",
        })
        cfg("switch", "recirculation", {
            "name": "Air Recirculation",
            "state_topic": f"{prefix}/{vin}/recirculation",
            "command_topic": f"{prefix}/{vin}/recirculation/set",
            "payload_on": "ON", "payload_off": "OFF", "icon": "mdi:autorenew",
        })

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

        # Charge limit / target SoC — writable HA `number` (#77, requested on FB). It's the
        # same value Mate already reads from the status config block (charge_limit_percent)
        # and sets from the Prepare-Car page (api.set_charge_limit). `charge_limit/set` routes
        # to the charge_limit command in the poller. Range matches the web UI (50–100%). Not
        # model-gated — the car's CHARGE_LIMIT right governs whether the set takes effect.
        cfg("number", "charge_limit", {
            "name": "Charge Limit",
            "state_topic": f"{prefix}/{vin}/charge_limit",
            "command_topic": f"{prefix}/{vin}/charge_limit/set",
            "min": 50, "max": 100, "step": 1,
            "unit_of_measurement": "%",
            "icon": "mdi:battery-charging-high",
            "mode": "slider",
        })

        # Charge schedule as a JSON text entity (#151, @chengler): meant for AUTOMATIONS —
        # {"start":"23:00","stop":"07:00","soc":90,"active":true,"days":"1,1,1,1,1,1,1"}. Every key
        # is optional and anything you omit keeps its current value, so an automation can send just
        # {"start":"23:00"}. The state echoes the plan Mate last wrote, so the box isn't blank.
        cfg("text", "charge_schedule", {
            "name": "Charge Schedule (JSON)",
            "state_topic": f"{prefix}/{vin}/charge_schedule",
            "command_topic": f"{prefix}/{vin}/charge_schedule/set",
            "icon": "mdi:calendar-clock",
            "mode": "text",
            "max": 255,
        })

        for key, name, icon in [
            # NB: no Lock/Unlock buttons here — superseded by the Door Lock lock entity
            # + Door Lock Toggle switch above (their retained configs are cleared below);
            # the raw "lock"/"unlock" command payloads STAY accepted for existing automations.
            ("open_trunk", "Open Trunk", "mdi:car-back"), ("close_trunk", "Close Trunk", "mdi:car-back"),
            # Windows (quick vent to 20%) + sunshade/roof — physical, so the car only acts on them in Park.
            ("open_windows", "Open Windows", "mdi:window-open-variant"),
            ("close_windows", "Close Windows", "mdi:window-closed-variant"),
            ("open_sunshade", "Open Sunshade", "mdi:window-shutter-open"),
            ("close_sunshade", "Close Sunshade", "mdi:window-shutter"),
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
            if capability_profile.command_shown(vin, key, self.get_setting, abilities=self.abilities, car_type=self.car_type):
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
