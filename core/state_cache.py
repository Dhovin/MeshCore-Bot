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

        self._state["lastUpdated"] = datetime.utcnow().isoformat() + "Z"

    def get_state(self):
        """
        Get a deep copy of the state cache to enforce read-only safety.
        """
        return copy.deepcopy(self._state)
