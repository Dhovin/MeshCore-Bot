import os
import sys
import logging
import asyncio
import importlib.util
from core.validator import validate as validate_schema

logger = logging.getLogger("ModuleManager")

class ModuleAPI:
    def __init__(self, module_name, bot):
        self.module_name = module_name
        self.bot = bot

    def subscribe(self, event_name, callback):
        """Subscribe to an event on the central Event Bus."""
        return self.bot.event_bus.subscribe(event_name, callback)

    async def send(self, command_string):
        """
        Execute a command directly on the connected hardware node.
        Sanitizes commands to prevent multi-line injection.
        """
        sanitized = self._sanitize_command(command_string)
        return await self.bot.connection_manager.execute(sanitized)

    def get_state(self):
        """Retrieve a read-only deep-copied snapshot of the state cache."""
        return self.bot.state_cache.get_state()

    def schedule_task(self, cron_expression, callback):
        """Schedule a task on the central task scheduler."""
        return self.bot.scheduler.schedule(cron_expression, callback, self.module_name)

    async def request_channel(self, channel_name):
        """
        Request a channel by name. If it does not exist on the node,
        automatically add/create it. Returns the channel index.
        """
        if not channel_name:
            return 0
            
        # If it's already an integer index
        if isinstance(channel_name, int):
            return channel_name
        if isinstance(channel_name, str) and channel_name.isdigit():
            return int(channel_name)
            
        # Ensure connection
        if not self.bot.connection_manager.isConnected or not self.bot.connection_manager.mc:
            logger.warning(f"Device not connected. Cannot request channel '{channel_name}'. Returning default index 0.")
            return 0
            
        # 1. Fetch channel list
        channels = await self.bot.connection_manager.execute("channels")
        if not isinstance(channels, list):
            logger.error(f"Failed to fetch channels list: {channels}")
            return 0
            
        # 2. Check if the channel already exists
        for ch in channels:
            if ch and ch.get("channel_name") == channel_name:
                return ch.get("channel_idx", 0)
                
        # 3. Channel does not exist, find first empty channel slot
        empty_idx = None
        for ch in channels:
            if ch and ch.get("channel_name") == "":
                empty_idx = ch.get("channel_idx")
                break
                
        if empty_idx is None:
            logger.error(f"No available empty channel slot to add '{channel_name}'")
            return 0
            
        # 4. Add/set the channel at the empty slot index
        logger.info(f"Channel '{channel_name}' requested by module '{self.module_name}' does not exist. Adding it at index {empty_idx}...")
        res = await self.bot.connection_manager.execute(["set_channel", str(empty_idx), channel_name])
        if isinstance(res, dict) and "error" in res:
            logger.error(f"Failed to create channel '{channel_name}': {res['error']}")
            return 0
            
        logger.info(f"Successfully added channel '{channel_name}' at index {empty_idx}")
        return empty_idx

    def _sanitize_command(self, cmd):
        if not isinstance(cmd, str):
            raise ValueError("Command must be a string")
        clean_cmd = cmd.strip()
        if '\n' in clean_cmd or '\r' in clean_cmd:
            raise ValueError("Command injection attempt detected: newlines are not allowed.")
        return clean_cmd

class ModuleManager:
    def __init__(self, bot):
        self.bot = bot
        self.modules = {}

    async def load_modules(self, modules_dir):
        """
        Scans modules_dir, imports modules dynamically, and runs schema validation.
        Protects against directory traversal.
        """
        resolved_base = os.path.abspath(modules_dir)

        if not os.path.exists(resolved_base):
            logger.warning(f"Modules directory not found: {resolved_base}")
            return

        for file in os.listdir(resolved_base):
            if not file.endswith('.py') or file == '__init__.py':
                continue

            full_path = os.path.abspath(os.path.join(resolved_base, file))

            # Directory traversal protection:
            # Ensure the resolved module file resides strictly inside the modules directory.
            if not full_path.startswith(resolved_base):
                logger.error(f"Traversal attempt blocked: {file}")
                continue

            module_name = os.path.splitext(file)[0]
            try:
                # Dynamic import
                spec = importlib.util.spec_from_file_location(module_name, full_path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)

                # Look for default exported Class (or standard module class structure)
                # Convention: a class named Module or named after the file in TitleCase
                class_name = ''.join(x.title() for x in module_name.split('_'))
                ModuleClass = getattr(module, class_name, None)
                if not ModuleClass:
                    # Fallback: check if there is a 'Module' class
                    ModuleClass = getattr(module, 'Module', None)

                if not ModuleClass:
                    logger.error(f"Module class not found in {file}. Expected name '{class_name}' or 'Module'.")
                    continue

                instance = ModuleClass()
                
                # Validate shape
                self._validate_module_shape(instance, file)

                # Get and validate configuration block
                module_config = self._get_and_validate_config(instance)

                # Inject API
                api = ModuleAPI(instance.name, self.bot)

                # Initialize
                logger.info(f"Initializing module: {instance.name}")
                init_hook = instance.init(api, module_config)
                if asyncio.iscoroutine(init_hook):
                    await init_hook

                self.modules[instance.name] = instance
            except Exception as e:
                logger.error(f"Failed to load module {file}: {e}", exc_info=True)

    async def start_modules(self):
        """Starts all successfully loaded modules."""
        for name, instance in self.modules.items():
            try:
                logger.info(f"Starting module: {name}")
                start_hook = instance.start()
                if asyncio.iscoroutine(start_hook):
                    await start_hook
            except Exception as e:
                logger.error(f"Error starting module '{name}': {e}", exc_info=True)

    async def stop_modules(self):
        """Stops all active modules, enforcing a 10-second timeout."""
        logger.info("Stopping active modules...")
        stop_tasks = {}

        for name, instance in self.modules.items():
            logger.info(f"Stopping module: {name}")
            try:
                stop_hook = instance.stop()
                if asyncio.iscoroutine(stop_hook):
                    stop_tasks[name] = asyncio.create_task(stop_hook)
            except Exception as e:
                logger.error(f"Error invoking stop hook for module '{name}': {e}", exc_info=True)

        if stop_tasks:
            try:
                # Await all stop hooks with a strict 10s timeout
                names = list(stop_tasks.keys())
                tasks = list(stop_tasks.values())
                results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=10.0)
                for module_name, res in zip(names, results):
                    if isinstance(res, Exception):
                        logger.error(f"Error during graceful stop of module '{module_name}': {res}", exc_info=res)
            except asyncio.TimeoutError:
                logger.warning("Graceful stop of modules timed out (10s limit).")
            except Exception as e:
                logger.error(f"Error gathering module stop hooks: {e}", exc_info=True)

        logger.info("All modules stopped.")

    def _validate_module_shape(self, instance, file_name):
        if not hasattr(instance, 'name') or not instance.name:
            raise ValueError(f"Module in {file_name} must have a non-empty 'name' attribute.")

        required_hooks = ['init', 'start', 'stop']
        for hook in required_hooks:
            if not hasattr(instance, hook) or not callable(getattr(instance, hook)):
                raise ValueError(f"Module '{instance.name}' is missing required hook: '{hook}()'.")

    def _get_and_validate_config(self, instance):
        config = self.bot.config or {}
        modules_config = config.get("modules", {})
        module_config = modules_config.get(instance.name, {})

        if hasattr(instance, 'config_schema') and instance.config_schema:
            errors = validate_schema(instance.config_schema, module_config, instance.name)
            if errors:
                raise ValueError(f"Configuration validation failed for module '{instance.name}':\n- " + "\n- ".join(errors))

        return module_config
