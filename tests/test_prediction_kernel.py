from __future__ import annotations

import asyncio
import unittest

from crunch_node.services.predict_components import PredictionKernel


class _Runner:
    def __init__(self):
        self.init_calls = 0
        self.calls: list[tuple[str, tuple]] = []
        self._sync_started = asyncio.Event()

    async def init(self):
        self.init_calls += 1

    async def sync(self):
        self._sync_started.set()
        await asyncio.Event().wait()

    async def call(self, method, args):
        self.calls.append((method, args))
        return {}


class _Arg:
    def __init__(self, name: str, type_: str):
        self.name = name
        self.type = type_


class TestPredictionKernel(unittest.IsolatedAsyncioTestCase):
    async def test_init_runner_only_initializes_once(self):
        runner = _Runner()
        kernel = PredictionKernel(
            runner=runner,
            proto_available=False,
        )

        await kernel.init_runner()
        await kernel.init_runner()

        self.assertEqual(runner.init_calls, 1)
        self.assertIsNotNone(kernel.runner_sync_task)

        await kernel.shutdown()

    async def test_call_uses_runner_with_encoded_args(self):
        runner = _Runner()
        kernel = PredictionKernel(
            runner=runner,
            proto_available=False,
        )
        await kernel.init_runner()

        args = kernel.encode_predict(
            scope={"subject": "ETH", "resolve_horizon_seconds": 30},
            call_args=[
                _Arg("subject", "STRING"),
                _Arg("resolve_horizon_seconds", "INT"),
                _Arg("step_seconds", "INT"),
            ],
            scope_defaults={
                "subject": "BTC",
                "resolve_horizon_seconds": 60,
                "step_seconds": 15,
            },
        )

        self.assertEqual(args, ("ETH", 30, 15))

        await kernel.call("predict", args)
        self.assertEqual(runner.calls, [("predict", ("ETH", 30, 15))])

        await kernel.shutdown()

    def test_encode_tick_in_raw_mode(self):
        kernel = PredictionKernel(runner=_Runner(), proto_available=False)
        payload = {"symbol": "BTC"}

        args = kernel.encode_tick(payload)

        self.assertEqual(args, (payload,))


if __name__ == "__main__":
    unittest.main()
