"""Client regression tests, no credentials or cloud access required."""
import asyncio
import pathlib
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

package = types.ModuleType("_mcc_test")
package.__path__ = [str(pathlib.Path(__file__).resolve().parents[1] /
                        "custom_components" / "midea_cloud_control")]
sys.modules[package.__name__] = package

from _mcc_test.client import CloudError, MideaCloudClient, StateResponse


def state(**kwargs):
    result = StateResponse.__new__(StateResponse)
    for field, value in {
        "power_on": True, "target_temperature": 23.5, "operational_mode": 4,
        "fan_speed": 102, "eco": False, "swing_mode": 0, "turbo": False,
        "fahrenheit": False, "sleep": False, "freeze_protection": None,
        "follow_me": False, "purifier": False, "target_humidity": None,
        "aux_heat": False, "independent_aux_heat": False, "display_on": True,
        **kwargs,
    }.items():
        setattr(result, field, value)
    return result


class ClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = MideaCloudClient("test@example.invalid", "not-a-password", "NetHome Plus")

    async def test_short_response_serializes(self):
        self.client._query_locked = AsyncMock(return_value=state())

        async def send(_, command):
            command.tobytes()  # actual library encoder, not a mock
            self.assertEqual(command.target_temperature, 24)
            self.assertEqual(command.operational_mode, 4)
            self.assertIsNotNone(command.target_humidity)
            return state(target_temperature=24)

        self.client._send = AsyncMock(side_effect=send)
        await self.client.set_state(1, target_temperature=24)

    async def test_concurrent_commands_do_not_overlap(self):
        current = state()
        order = []

        async def query(_):
            order.append("query")
            await asyncio.sleep(0)
            return current

        async def send(_, command):
            order.append("send")
            await asyncio.sleep(0)
            current.target_temperature = command.target_temperature
            current.fan_speed = command.fan_speed
            return current

        self.client._query_locked = AsyncMock(side_effect=query)
        self.client._send = AsyncMock(side_effect=send)
        await asyncio.gather(self.client.set_state(1, target_temperature=24),
                             self.client.set_state(1, fan_speed=60))
        self.assertEqual(order, ["query", "send", "query", "send"])
        self.assertEqual(current.target_temperature, 24)
        self.assertEqual(current.fan_speed, 60)

    async def test_read_reconnects_once(self):
        self.client._query_once = AsyncMock(side_effect=[CloudError("offline"), state()])
        self.client.connect = AsyncMock()
        await self.client.query(1)
        self.client.connect.assert_awaited_once()
        self.assertEqual(self.client._query_once.await_count, 2)

    async def test_write_not_repeated(self):
        self.client._query_locked = AsyncMock(return_value=state())
        self.client._send = AsyncMock(side_effect=CloudError("no acknowledgement"))
        with self.assertRaises(CloudError):
            await self.client.set_state(1, target_temperature=24)
        self.client._send.assert_awaited_once()

    async def test_display_already_correct_no_write(self):
        self.client._query_locked = AsyncMock(return_value=state(display_on=True))
        self.client._send = AsyncMock()
        await self.client.set_display(1, True)
        self.client._send.assert_not_awaited()

    async def test_display_timeout_does_not_repeat_toggle(self):
        self.client._query_locked = AsyncMock(side_effect=[state(), state(display_on=False)])
        self.client._send = AsyncMock(side_effect=CloudError("no acknowledgement"))
        result = await self.client.set_display(1, False)
        self.assertFalse(result.display_on)
        self.client._send.assert_awaited_once()

    async def test_eco_delayed_confirmation_and_conflicts(self):
        self.client._query_locked = AsyncMock(side_effect=[
            state(operational_mode=2, turbo=True, sleep=True),
            state(operational_mode=2, eco=False),
            state(operational_mode=2, eco=True),
        ])

        async def send(_, command):
            self.assertTrue(command.eco)
            self.assertFalse(command.turbo)
            self.assertFalse(command.sleep)
            self.assertFalse(command.freeze_protection)
            return state(operational_mode=2, eco=False)

        self.client._send = AsyncMock(side_effect=send)
        with patch("_mcc_test.client.asyncio.sleep", new_callable=AsyncMock):
            result = await self.client.set_state(1, verify=True, eco=True)
        self.assertTrue(result.eco)
        self.client._send.assert_awaited_once()
        self.assertEqual(self.client._query_locked.await_count, 3)

    async def test_unconfirmed_display_returns_actual_state(self):
        self.client._query_locked = AsyncMock(return_value=state(display_on=True))
        self.client._send = AsyncMock(return_value=state())
        with patch("_mcc_test.client.asyncio.sleep", new_callable=AsyncMock):
            result = await self.client.set_display(1, False)
        self.assertTrue(result.display_on)
        self.client._send.assert_awaited_once()
        self.assertEqual(self.client._query_locked.await_count, 4)


if __name__ == "__main__":
    unittest.main()
