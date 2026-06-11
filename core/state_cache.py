import copy
from datetime import datetime

class StateCache:
    def __init__(self):
        self._state = {
            "battery": None,
            "neighbors": [],
            "neighborCount": 0,
            "uptime": None,
            "fwVersion": None,
            "model": None,
            "radio_freq": None,
            "radio_bw": None,
            "radio_sf": None,
            "radio_cr": None,
            "noise_floor": None,
            "deviceName": None,
            "publicKey": None,
            "lastUpdated": None,
            "connectionStatus": "disconnected",
            "timeSynced": False,
        }

    def update(self, key, value):
        """
        Update a specific property in the cache.
        """
        if key in self._state:
            self._state[key] = value
            self._state["lastUpdated"] = datetime.utcnow().isoformat() + "Z"

    def update_from_telemetry(self, telemetry):
        """
        Bulk update state values from parsed telemetry dictionary.
        """
        if not isinstance(telemetry, dict):
            return

        if "battery" in telemetry:
            self._state["battery"] = telemetry["battery"]
        if "uptime" in telemetry:
            self._state["uptime"] = telemetry["uptime"]
        if "neighbors" in telemetry and isinstance(telemetry["neighbors"], list):
            self._state["neighbors"] = list(telemetry["neighbors"])
            self._state["neighborCount"] = len(telemetry["neighbors"])
        if "model" in telemetry:
            self._state["model"] = telemetry["model"]
        if "ver" in telemetry:
            self._state["fwVersion"] = telemetry["ver"]
        elif "fw_ver" in telemetry:
            self._state["fwVersion"] = telemetry["fw_ver"]
        elif "fw ver" in telemetry:
            self._state["fwVersion"] = telemetry["fw ver"]

        # Parse radio settings
        if "radio_freq" in telemetry:
            self._state["radio_freq"] = telemetry["radio_freq"]
        if "radio_bw" in telemetry:
            self._state["radio_bw"] = telemetry["radio_bw"]
        if "radio_sf" in telemetry:
            self._state["radio_sf"] = telemetry["radio_sf"]
        if "radio_cr" in telemetry:
            self._state["radio_cr"] = telemetry["radio_cr"]

        # Parse device name and public key
        if "name" in telemetry:
            self._state["deviceName"] = telemetry["name"]
        if "public_key" in telemetry:
            pubkey = telemetry["public_key"]
            if isinstance(pubkey, bytes):
                pubkey = pubkey.hex()
            self._state["publicKey"] = pubkey

        self._state["lastUpdated"] = datetime.utcnow().isoformat() + "Z"

    def get_state(self):
        """
        Get a deep copy of the state cache to enforce read-only safety.
        """
        return copy.deepcopy(self._state)
