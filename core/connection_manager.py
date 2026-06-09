import time
import json
import shlex
import logging
import asyncio
import serial.tools.list_ports
from meshcore.meshcore import MeshCore
from meshcore.events import EventType

logger = logging.getLogger("ConnectionManager")

try:
    from bleak import BleakScanner
    BLE_AVAILABLE = True
except ImportError:
    BLE_AVAILABLE = False

class ConnectionManager:
    def __init__(self, bot):
        self.bot = bot
        self.mc = None
        self.isConnected = False
        self.connectionType = None
        self.deviceInfo = None

    async def connect(self):
        """
        Connect to the hardware node.
        Attempts Serial auto-discovery first, then BLE, then falls back to TCP.
        """
        conn_config = self.bot.config.get("connection", {})
        conn_type = conn_config.get("type", "auto")

        logger.info(f"Starting connection sequence (type: {conn_type})")

        target_port = conn_config.get("port")
        target_address = conn_config.get("address")
        baudrate = conn_config.get("baudrate", 115200)

        final_type = conn_type

        if conn_type == 'auto':
            logger.info("Running device auto-discovery...")
            
            # 1. Serial Discovery
            ports = list(serial.tools.list_ports.comports())
            if ports:
                # Filter for typical USB serial adapters
                usb_ports = []
                for p in ports:
                    desc = p.description.lower()
                    if any(x in desc for x in ['cp210', 'ch34', 'ftdi', 'usb', 'serial', 'uart']):
                        usb_ports.append(p)
                
                selected = usb_ports[0] if usb_ports else ports[0]
                logger.info(f"Auto-discovered Serial port: {selected.device} ({selected.description})")
                final_type = 'serial'
                target_port = selected.device
            
            # 2. BLE Discovery
            elif BLE_AVAILABLE:
                logger.info("No serial port found. Scanning BLE for companion nodes...")
                try:
                    devices = await BleakScanner.discover(timeout=3.0)
                    meshcore_ble = [d for d in devices if d.name and d.name.startswith("MeshCore-")]
                    if meshcore_ble:
                        selected = meshcore_ble[0]
                        logger.info(f"Auto-discovered BLE device: {selected.name} ({selected.address})")
                        final_type = 'ble'
                        target_address = selected.address
                except Exception as e:
                    logger.warning(f"BLE scan failed: {e}")
            
            # 3. Fallback to TCP if config has defaults
            if final_type == 'auto':
                host = conn_config.get("host")
                tcp_port = conn_config.get("tcpPort")
                if host and tcp_port:
                    logger.info(f"Auto-discovery fallback to TCP: {host}:{tcp_port}")
                    final_type = 'tcp'
                else:
                    raise RuntimeError("Auto-discovery failed: No Serial or BLE companion nodes detected, and no TCP host configured.")

        self.connectionType = final_type

        # Construct Native MeshCore connection
        if final_type == 'serial':
            if not target_port:
                raise ValueError("Serial port is required but not specified.")
            logger.info(f"Connecting to Serial port: {target_port} ({baudrate} baud)")
            self.mc = await MeshCore.create_serial(port=target_port, baudrate=baudrate, only_error=True)
        elif final_type == 'ble':
            logger.info(f"Connecting to BLE device: {target_address or 'Auto-scan'}")
            # If target_address is empty, create_ble will scan and pick first
            self.mc = await MeshCore.create_ble(address=target_address, only_error=True)
        elif final_type == 'tcp':
            host = conn_config.get("host", "127.0.0.1")
            tcp_port = conn_config.get("tcpPort", 5000)
            logger.info(f"Connecting to TCP: {host}:{tcp_port}")
            self.mc = await MeshCore.create_tcp(host=host, port=tcp_port, only_error=True)
        else:
            raise ValueError(f"Unsupported connection type: {final_type}")

        if not self.mc:
            raise RuntimeError("Failed to create MeshCore connection instance.")

        # Bind event subscriptions
        self._subscribe_events()

        # Run connection handshake
        await self._run_handshake()

    async def disconnect(self):
        """Disconnect and release the node serial/BLE interface."""
        if self.mc:
            logger.info("Closing connection to hardware node...")
            try:
                res = self.mc.stop()
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.error(f"Error while stopping meshcore client: {e}")
            self.isConnected = False
            self.mc = None

    async def execute(self, cmd_str):
        """
        Execute command string natively on the client.
        Provides compatibility with string-based Module API commands.
        Attempts to delegate to the official meshcore-cli commands parser if available.
        """
        if not self.mc or not self.isConnected:
            return {"error": "Device not connected"}

        if isinstance(cmd_str, (list, tuple)):
            cmds = list(cmd_str)
        else:
            try:
                cmds = shlex.split(cmd_str)
            except Exception as e:
                return {"error": f"Invalid command encoding: {e}"}

        if not cmds:
            return {"error": "Empty command"}

        # 1. Attempt to delegate to the official meshcore-cli source if available
        import sys
        import os
        import io
        import contextlib

        next_cmd = None
        try:
            from meshcore_cli.meshcore_cli import next_cmd as imported_cmd
            next_cmd = imported_cmd
        except ImportError:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.abspath(os.path.join(script_dir, ".."))
            cli_src_path = os.path.abspath(os.path.join(project_root, "../meshcore-cli/src"))
            if os.path.exists(cli_src_path):
                if cli_src_path not in sys.path:
                    sys.path.insert(0, cli_src_path)
                try:
                    from meshcore_cli.meshcore_cli import next_cmd as imported_cmd
                    next_cmd = imported_cmd
                except ImportError:
                    pass

        if next_cmd is not None:
            try:
                # Capture standard output from next_cmd execution
                f = io.StringIO()
                with contextlib.redirect_stdout(f):
                    clean_cmds = list(cmds)
                    if clean_cmds[0].startswith('$'):
                        clean_cmds[0] = clean_cmds[0][1:]
                    if clean_cmds[0].startswith('.'):
                        clean_cmds[0] = clean_cmds[0][1:]
                    
                    await next_cmd(self.mc, clean_cmds, json_output=True)
                
                output_str = f.getvalue().strip()
                if not output_str:
                    return {"ok": True}
                
                # Try parsing entire output as a single JSON object
                try:
                    return json.loads(output_str)
                except json.JSONDecodeError:
                    pass
                
                # Try parsing line-by-line in case of multiple JSON logs
                lines = output_str.split("\n")
                parsed_lines = []
                is_all_json = True
                for line in lines:
                    line_str = line.strip()
                    if not line_str:
                        continue
                    try:
                        parsed_lines.append(json.loads(line_str))
                    except json.JSONDecodeError:
                        is_all_json = False
                        break
                
                if is_all_json and parsed_lines:
                    if len(parsed_lines) == 1:
                        return parsed_lines[0]
                    return parsed_lines
                
                return {"output": output_str}
            except Exception as e:
                logger.warning(f"Failed to delegate command to meshcore-cli source: {e}. Falling back to internal commands.", exc_info=True)

        # 2. Fallback execution logic for standalone deployment
        cmd = cmds[0]
        if cmd.startswith('$'):
            cmd = cmd[1:]
        if cmd.startswith('.'):
            cmd = cmd[1:]
        cmd = cmd.lower()

        try:
            if cmd == "infos" or cmd == "i":
                res = await self.mc.commands.send_device_query()
                return res.payload
            elif cmd == "self_telemetry" or cmd == "t":
                res = await self.mc.commands.get_self_telemetry()
                return res.payload
            elif cmd == "clock":
                res = await self.mc.commands.get_time()
                return res.payload
            elif cmd == "reboot":
                res = await self.mc.commands.reboot()
                return res.payload
            elif cmd == "clock sync" or (cmd == "clock" and len(cmds) > 1 and cmds[1] == "sync"):
                res = await self.mc.commands.set_time(int(time.time()))
                if res.type == EventType.ERROR:
                    return {"error": "Failed to sync time on node"}
                return {"ok": "time synced"}
            elif cmd == "msg" or cmd == "m":
                if len(cmds) < 3:
                    return {"error": "Usage: msg <recipient_name> <message>"}
                recipient = cmds[1]
                text = cmds[2]

                contact = self.mc.get_contact_by_name(recipient)
                if not contact:
                    return {"error": f"Unknown contact: {recipient}"}

                res = await self.mc.commands.send_msg(contact, text)
                if res.type == EventType.ERROR:
                    return {"error": f"Error sending message: {res}"}
                
                payload = dict(res.payload)
                if "expected_ack" in payload and isinstance(payload["expected_ack"], bytes):
                    payload["expected_ack"] = payload["expected_ack"].hex()
                return payload
            elif cmd == "chan" or cmd == "ch":
                if len(cmds) < 3:
                    return {"error": "Usage: chan <channel_idx_or_name> <message>"}
                chan_arg = cmds[1]
                text = cmds[2]

                if chan_arg.isdigit():
                    nb = int(chan_arg)
                else:
                    chan = self._get_channel_by_name(chan_arg)
                    if not chan:
                        return {"error": f"Unknown channel name: {chan_arg}"}
                    nb = chan.get("channel_idx", 0)

                res = await self.mc.commands.send_chan_msg(nb, text)
                if res.type == EventType.ERROR:
                    return {"error": f"Error sending channel message: {res}"}
                return res.payload
            elif cmd == "set":
                if len(cmds) < 3:
                    return {"error": "Usage: set <name|tx|pin|radio> <value>"}
                setting = cmds[1].lower()
                value = cmds[2]
                if setting == "name":
                    res = await self.mc.commands.set_name(value)
                    if res.type == EventType.ERROR:
                        return {"error": f"Failed to set name: {res}"}
                    return res.payload
                elif setting == "tx" or setting == "tx_power":
                    res = await self.mc.commands.set_tx_power(value)
                    if res.type == EventType.ERROR:
                        return {"error": f"Failed to set TX power: {res}"}
                    return res.payload
                elif setting == "pin":
                    res = await self.mc.commands.set_devicepin(value)
                    if res.type == EventType.ERROR:
                        return {"error": f"Failed to set pin: {res}"}
                    return res.payload
                elif setting == "radio":
                    params = value.split(",")
                    if len(params) > 4:
                        repeat = params[4] in ("repeat", "on", "1", "yes")
                        res = await self.mc.commands.set_radio(params[0], params[1], params[2], params[3], repeat)
                    else:
                        res = await self.mc.commands.set_radio(*params)
                    if res.type == EventType.ERROR:
                        return {"error": f"Failed to set radio config: {res}"}
                    return res.payload
                else:
                    return {"error": f"Unsupported setting: {setting}"}
            elif cmd in ("get_channels", "channels", "chans"):
                ch = 0
                channels = []
                while True:
                    res = await self.mc.commands.get_channel(ch)
                    if res.type == EventType.ERROR:
                        break
                    info = dict(res.payload)
                    if "channel_secret" in info and isinstance(info["channel_secret"], bytes):
                        info["channel_secret"] = info["channel_secret"].hex()
                    channels.append(info)
                    ch += 1
                self.mc.channels = channels
                return channels
            elif cmd in ("set_channel", "add_channel"):
                if len(cmds) < 3:
                    return {"error": "Usage: set_channel <idx_or_name> <name> [key_hex]"}
                chan_arg = cmds[1]
                name_arg = cmds[2]
                key_arg = None
                if len(cmds) > 3:
                    try:
                        key_arg = bytes.fromhex(cmds[3])
                    except ValueError:
                        return {"error": "Key must be a valid hex string"}
                if chan_arg.isdigit():
                    nb = int(chan_arg)
                else:
                    if not hasattr(self.mc, 'channels') or not self.mc.channels:
                        await self.execute("channels")
                    chan = self._get_channel_by_name(chan_arg)
                    if not chan:
                        nb = len(getattr(self.mc, 'channels', []))
                    else:
                        nb = chan.get("channel_idx", 0)
                res = await self.mc.commands.set_channel(nb, name_arg, key_arg)
                if res.type == EventType.ERROR:
                    return {"error": f"Failed to set channel: {res}"}
                res_info = await self.mc.commands.get_channel(nb)
                if res_info.type == EventType.ERROR:
                    return {"error": f"Failed to retrieve updated channel info: {res_info}"}
                info = dict(res_info.payload)
                if "channel_secret" in info and isinstance(info["channel_secret"], bytes):
                    info["channel_secret"] = info["channel_secret"].hex()
                if not hasattr(self.mc, 'channels'):
                    self.mc.channels = []
                while len(self.mc.channels) <= nb:
                    self.mc.channels.append({})
                self.mc.channels[nb] = info
                return info
            elif cmd == "remove_channel":
                if len(cmds) < 2:
                    return {"error": "Usage: remove_channel <idx_or_name>"}
                chan_arg = cmds[1]
                if chan_arg.isdigit():
                    nb = int(chan_arg)
                else:
                    if not hasattr(self.mc, 'channels') or not self.mc.channels:
                        await self.execute("channels")
                    chan = self._get_channel_by_name(chan_arg)
                    if not chan:
                        return {"error": f"Unknown channel: {chan_arg}"}
                    nb = chan.get("channel_idx", 0)
                empty_key = bytes.fromhex(16 * "00")
                res = await self.mc.commands.set_channel(nb, "", empty_key)
                if res.type == EventType.ERROR:
                    return {"error": f"Failed to remove channel: {res}"}
                res_info = await self.mc.commands.get_channel(nb)
                if res_info.type != EventType.ERROR:
                    info = dict(res_info.payload)
                    if "channel_secret" in info and isinstance(info["channel_secret"], bytes):
                        info["channel_secret"] = info["channel_secret"].hex()
                    if hasattr(self.mc, 'channels') and nb < len(self.mc.channels):
                        self.mc.channels[nb] = info
                return {"ok": f"channel {nb} removed"}
            else:
                return {"error": f"Unsupported command: {cmd}"}
        except Exception as e:
            cmd_log_str = " ".join(cmds) if isinstance(cmd_str, (list, tuple)) else cmd_str
            logger.error(f"Error executing command '{cmd_log_str}': {e}", exc_info=True)
            return {"error": str(e)}

    async def sync_time(self):
        """Sync node RTC with host system time."""
        logger.info("Synchronizing node RTC clock...")
        try:
            res = await self.mc.commands.set_time(int(time.time()))
            if res.type == EventType.ERROR:
                logger.error("RTC Clock synchronization failed.")
            else:
                self.bot.state_cache.update("timeSynced", True)
                logger.info("RTC Clock successfully synchronized with host system time.")
                self.bot.event_bus.publish("time_sync", {"ok": "time synced"})
        except Exception as e:
            logger.error(f"Error syncing time: {e}", exc_info=True)

    def _subscribe_events(self):
        # Native registrations
        self.mc.subscribe(EventType.CONTACT_MSG_RECV, self._on_private_message)
        self.mc.subscribe(EventType.CHANNEL_MSG_RECV, self._on_channel_message)
        self.mc.subscribe(EventType.ADVERTISEMENT, self._on_advertisement)
        self.mc.subscribe(EventType.PATH_UPDATE, self._on_path_update)
        self.mc.subscribe(EventType.NEW_CONTACT, self._on_new_contact)
        self.mc.subscribe(EventType.DISCONNECTED, self._on_disconnect)

    async def _run_handshake(self):
        # Establish background contact loading
        await self.mc.ensure_contacts()
        self._load_contacts()
        self._save_contacts()
        await self.mc.start_auto_message_fetching()

        logger.info("Executing connection handshake device query...")
        res = await self.mc.commands.send_device_query()
        if res.type == EventType.ERROR:
            raise RuntimeError(f"Handshake device query failed: {res}")

        self.deviceInfo = res.payload
        self.isConnected = True

        # Update cache
        self.bot.state_cache.update("connectionStatus", "connected")
        self.bot.state_cache.update_from_telemetry(res.payload)

        logger.info(f"Handshake complete. Connected to node: {self.mc.self_info.get('name', 'Unknown')}")
        self.bot.event_bus.publish("connect", res.payload)

        # Trigger clock sync immediately
        await self.sync_time()

    def _on_private_message(self, event):
        try:
            payload = event.payload or {}
            msg = {
                "sender": payload.get("sender") or payload.get("from") or "unknown",
                "text": payload.get("message") or payload.get("crypted") or "",
                "channel": 0,
                "timestamp": payload.get("time") or int(time.time()),
                "snr": payload.get("snr"),
                "rssi": payload.get("rssi"),
                "path": payload.get("path", [])
            }
            self.bot.event_bus.publish("message", msg)
        except Exception as e:
            logger.error(f"Error handling private message event: {e}", exc_info=True)

    def _on_channel_message(self, event):
        try:
            payload = event.payload or {}
            msg = {
                "sender": payload.get("sender") or payload.get("from") or "unknown",
                "text": payload.get("message") or payload.get("crypted") or "",
                "channel": payload.get("chan_name") or payload.get("chan_nb") or 0,
                "timestamp": payload.get("time") or int(time.time()),
                "snr": payload.get("snr"),
                "rssi": payload.get("rssi"),
                "path": payload.get("path", [])
            }
            self.bot.event_bus.publish("message", msg)
        except Exception as e:
            logger.error(f"Error handling channel message event: {e}", exc_info=True)

    def _on_advertisement(self, event):
        try:
            self.bot.event_bus.publish("advert", event.payload)
            payload = event.payload or {}
            pubkey = payload.get("public_key")
            if pubkey:
                if hasattr(self, 'mc') and self.mc:
                    if not hasattr(self.mc, '_contacts') or self.mc._contacts is None:
                        self.mc._contacts = {}
                    
                    if pubkey not in self.mc._contacts:
                        self.mc._contacts[pubkey] = {
                            "public_key": pubkey,
                            "type": payload.get("type", 1),
                            "flags": payload.get("flags", 0),
                            "adv_name": payload.get("adv_name") or payload.get("name") or f"Unknown-{pubkey[:6]}",
                            "last_advert": payload.get("last_advert") or int(time.time()),
                            "adv_lat": payload.get("adv_lat", 0.0),
                            "adv_lon": payload.get("adv_lon", 0.0),
                            "lastmod": payload.get("lastmod") or int(time.time())
                        }
                    else:
                        contact = self.mc._contacts[pubkey]
                        if payload.get("adv_name") or payload.get("name"):
                            contact["adv_name"] = payload.get("adv_name") or payload.get("name")
                        contact["last_advert"] = payload.get("last_advert") or int(time.time())
                        if "adv_lat" in payload:
                            contact["adv_lat"] = payload["adv_lat"]
                        if "adv_lon" in payload:
                            contact["adv_lon"] = payload["adv_lon"]
                        contact["lastmod"] = payload.get("lastmod") or int(time.time())
                self._save_contacts()
        except Exception as e:
            logger.error(f"Error handling advertisement event: {e}", exc_info=True)

    def _on_path_update(self, event):
        try:
            self.bot.event_bus.publish("path_update", event.payload)
        except Exception as e:
            logger.error(f"Error handling path update event: {e}", exc_info=True)

    def _on_new_contact(self, event):
        try:
            self.bot.event_bus.publish("new_contact", event.payload)
            self._save_contacts()
        except Exception as e:
            logger.error(f"Error handling new contact event: {e}", exc_info=True)

    def _on_disconnect(self, event):
        try:
            logger.critical("Connection lost to companion node! Triggering graceful shutdown...")
            self.isConnected = False
            self.bot.state_cache.update("connectionStatus", "disconnected")
            self.bot.event_bus.publish("disconnect", event.payload)
            self.bot.loop.call_soon_threadsafe(self.bot.shutdown_event.set)
        except Exception as e:
            logger.error(f"Error handling disconnect event: {e}", exc_info=True)

    def _get_channel_by_name(self, name):
        channels = getattr(self.mc, 'channels', [])
        for ch in channels:
            if ch.get("channel_name") == name:
                return ch
        return None

    def _load_contacts(self):
        """Load persistent contacts from config/contacts.json into self.mc._contacts."""
        import os
        contacts_file = os.path.abspath("config/contacts.json")
        if not os.path.exists(contacts_file):
            return
        try:
            with open(contacts_file, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            
            if not hasattr(self, 'mc') or not self.mc:
                return

            if not hasattr(self.mc, '_contacts') or self.mc._contacts is None:
                self.mc._contacts = {}

            loaded_count = 0
            for k, v in saved.items():
                if k not in self.mc._contacts:
                    self.mc._contacts[k] = v
                    loaded_count += 1
                else:
                    new_lastmod = self.mc._contacts[k].get("lastmod", 0)
                    old_lastmod = v.get("lastmod", 0)
                    if old_lastmod > new_lastmod:
                        self.mc._contacts[k] = v
                        loaded_count += 1
            
            logger.info(f"Loaded {loaded_count} new/updated persistent contacts into memory.")
        except Exception as e:
            logger.error(f"Error loading persistent contacts: {e}", exc_info=True)

    def _save_contacts(self):
        """Save local contacts copy to config/contacts.json."""
        import os
        contacts_file = os.path.abspath("config/contacts.json")
        try:
            existing_contacts = {}
            if os.path.exists(contacts_file):
                try:
                    with open(contacts_file, 'r', encoding='utf-8') as f:
                        existing_contacts = json.load(f)
                except Exception:
                    pass

            merged = {}
            if hasattr(self, 'mc') and self.mc and hasattr(self.mc, '_contacts') and self.mc._contacts:
                for k, v in self.mc._contacts.items():
                    merged[k] = dict(v)

            for k, v in existing_contacts.items():
                if k not in merged:
                    merged[k] = v
                else:
                    new_lastmod = merged[k].get("lastmod", 0)
                    old_lastmod = v.get("lastmod", 0)
                    if old_lastmod > new_lastmod:
                        merged[k] = v

            os.makedirs(os.path.dirname(contacts_file), exist_ok=True)
            with open(contacts_file, 'w', encoding='utf-8') as f:
                json.dump(merged, f, indent=2)
            
            logger.info(f"Saved {len(merged)} contacts persistently to {contacts_file}.")
        except Exception as e:
            logger.error(f"Error saving contacts: {e}", exc_info=True)
