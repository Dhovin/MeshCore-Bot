import unittest
import asyncio
from datetime import datetime
from core.event_bus import EventBus
from core.state_cache import StateCache
from core.scheduler import Scheduler, parse_field
from core.validator import validate as validate_schema

class TestEventBus(unittest.TestCase):
    def setUp(self):
        self.eb = EventBus()

    def test_sync_subscription_and_publish(self):
        received = []
        def handler(data):
            received.append(data)
            
        unsub = self.eb.subscribe("test_event", handler)
        self.eb.publish("test_event", "hello")
        self.assertEqual(received, ["hello"])
        
        unsub()
        self.eb.publish("test_event", "world")
        self.assertEqual(received, ["hello"]) # unchanged since unsubscribed

    def test_async_subscription(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            received = []
            async def handler(data):
                received.append(data)
                
            self.eb.subscribe("async_event", handler)
            
            async def run_test():
                self.eb.publish("async_event", "async_payload")
                await asyncio.sleep(0.05)
                
            loop.run_until_complete(run_test())
            self.assertEqual(received, ["async_payload"])
        finally:
            loop.close()

    def test_sync_listener_exception_resilience(self):
        # Verify that if a listener raises an exception, the event bus continues to process other listeners
        received = []
        def bad_listener(data):
            raise RuntimeError("Intended test crash")
        def good_listener(data):
            received.append(data)
            
        self.eb.subscribe("test_err_event", bad_listener)
        self.eb.subscribe("test_err_event", good_listener)
        
        try:
            self.eb.publish("test_err_event", "resilient_payload")
        except Exception as e:
            self.fail(f"EventBus publish raised exception: {e}")
            
        self.assertEqual(received, ["resilient_payload"])

    def test_async_listener_exception_resilience(self):
        # Verify that an async listener raising an exception is caught safely inside the EventBus task loop wrapper
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            received = []
            async def bad_async_listener(data):
                raise RuntimeError("Intended test async crash")
            async def good_async_listener(data):
                received.append(data)
                
            self.eb.subscribe("async_err_event", bad_async_listener)
            self.eb.subscribe("async_err_event", good_async_listener)
            
            async def run_test():
                self.eb.publish("async_err_event", "async_resilient_payload")
                await asyncio.sleep(0.05)
                
            loop.run_until_complete(run_test())
            self.assertEqual(received, ["async_resilient_payload"])
        finally:
            loop.close()

class TestStateCache(unittest.TestCase):
    def setUp(self):
        self.cache = StateCache()

    def test_update_and_get_state(self):
        self.cache.update("battery", 85)
        state = self.cache.get_state()
        self.assertEqual(state["battery"], 85)
        self.assertIsNotNone(state["lastUpdated"])
        
        # Verify read-only safety (modifying the returned copy shouldn't mutate cache)
        state["battery"] = 99
        self.assertEqual(self.cache.get_state()["battery"], 85)

    def test_update_from_telemetry(self):
        tel = {
            "battery": 92,
            "uptime": 3600,
            "neighbors": ["node1", "node2"],
            "model": "T-Echo",
            "ver": "3.1.0"
        }
        self.cache.update_from_telemetry(tel)
        state = self.cache.get_state()
        self.assertEqual(state["battery"], 92)
        self.assertEqual(state["uptime"], 3600)
        self.assertEqual(state["neighborCount"], 2)
        self.assertEqual(state["neighbors"], ["node1", "node2"])
        self.assertEqual(state["model"], "T-Echo")
        self.assertEqual(state["fwVersion"], "3.1.0")

class TestScheduler(unittest.TestCase):
    def test_parse_field_wildcard(self):
        matcher = parse_field('*', 0, 59)
        self.assertTrue(matcher(0))
        self.assertTrue(matcher(30))
        self.assertTrue(matcher(59))

    def test_parse_field_ranges_and_steps(self):
        # Step: every 5 minutes
        matcher = parse_field('*/5', 0, 59)
        self.assertTrue(matcher(0))
        self.assertTrue(matcher(5))
        self.assertTrue(matcher(10))
        self.assertFalse(matcher(3))
        
        # Range with step
        matcher = parse_field('10-20/2', 0, 59)
        self.assertTrue(matcher(10))
        self.assertTrue(matcher(12))
        self.assertFalse(matcher(8))
        self.assertFalse(matcher(22))
        
        # Lists
        matcher = parse_field('1,3,5', 0, 59)
        self.assertTrue(matcher(1))
        self.assertTrue(matcher(3))
        self.assertFalse(matcher(2))

    def test_cron_matching(self):
        sched = Scheduler()
        called = False
        def task():
            nonlocal called
            called = True
            
        sched.schedule("15 10 * * *", task)
        
        matching_time = datetime(2026, 6, 8, 10, 15, 0)
        non_matching_time = datetime(2026, 6, 8, 10, 16, 0)
        
        self.assertTrue(sched.tasks[0]["match"](matching_time))
        self.assertFalse(sched.tasks[0]["match"](non_matching_time))

    def test_scheduler_tick_exception_resilience(self):
        sched = Scheduler()
        sync_called = False
        async_called = False
        
        def bad_sync_task():
            raise RuntimeError("Intended sync schedule crash")
            
        async def bad_async_task():
            raise RuntimeError("Intended async schedule crash")
            
        def good_sync_task():
            nonlocal sync_called
            sync_called = True
            
        async def good_async_task():
            nonlocal async_called
            async_called = True
            
        sched.schedule("* * * * *", bad_sync_task, name="bad_sync")
        sched.schedule("* * * * *", bad_async_task, name="bad_async")
        sched.schedule("* * * * *", good_sync_task, name="good_sync")
        sched.schedule("* * * * *", good_async_task, name="good_async")
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def run_test():
                sched._tick()
                await asyncio.sleep(0.05)
            loop.run_until_complete(run_test())
            
            self.assertTrue(sync_called)
            self.assertTrue(async_called)
        finally:
            loop.close()

class TestValidator(unittest.TestCase):
    def test_validator_types(self):
        schema = {
            "type": "object",
            "properties": {
                "val_str": {"type": "string"},
                "val_int": {"type": "integer"},
                "val_bool": {"type": "boolean"},
                "val_num": {"type": "number"}
            },
            "required": ["val_str", "val_int"]
        }
        
        # Valid data
        valid_data = {
            "val_str": "hello",
            "val_int": 42,
            "val_bool": True,
            "val_num": 3.14
        }
        errors = validate_schema(schema, valid_data)
        self.assertEqual(errors, [])
        
        # Invalid data
        invalid_data = {
            "val_str": 123,
            "val_int": "forty-two",
            "val_bool": "true",
            "val_num": False
        }
        errors = validate_schema(schema, invalid_data)
        self.assertEqual(len(errors), 4)

    def test_validator_required_missing(self):
        schema = {
            "type": "object",
            "required": ["key_a", "key_b"]
        }
        data = {
            "key_a": 1
        }
        errors = validate_schema(schema, data)
        self.assertEqual(errors, ["Path 'key_b' is required"])

if __name__ == '__main__':
    unittest.main()
