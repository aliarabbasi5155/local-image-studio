import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from scripts.engine_supervisor import EngineSupervisor
from studio.native import NativeEngine


class SupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'runtime').mkdir()
        self.engine = EngineSupervisor(self.root)
        self.process = Mock(returncode=None)
        self.process.wait = AsyncMock(return_value=0)
        self.engine.process = self.process
        self.engine.started = time.monotonic()
        self.engine.last_activity = time.monotonic() - 601
        self.engine.probe = AsyncMock(side_effect=self.probe)
        self.queue = {'queue_running': [], 'queue_pending': []}

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def probe(self, path):
        return {'system': {'argv': [str(self.root / 'outputs')]}} if path == '/system_stats' else self.queue

    async def test_idle_shutdown_releases_owned_process(self):
        await self.engine.check_idle()
        self.process.terminate.assert_called_once()
        self.process.wait.assert_awaited_once()
        self.assertIsNone(self.engine.process)

    async def test_running_and_pending_work_never_stops(self):
        for key in ('queue_running', 'queue_pending'):
            self.queue = {'queue_running': [], 'queue_pending': [], key: [[1, 'job']]}
            self.engine.last_activity = time.monotonic() - 900
            await self.engine.check_idle()
            self.process.terminate.assert_not_called()
            self.assertLess(time.monotonic() - self.engine.last_activity, 1)

    async def test_missing_queue_is_not_idle(self):
        self.queue = {}
        await self.engine.check_idle()
        self.process.terminate.assert_not_called()

    async def test_wake_reserves_engine_during_preparation(self):
        await self.engine.wake()
        await self.engine.check_idle()
        self.process.terminate.assert_not_called()

    async def test_external_engine_is_never_stopped(self):
        self.engine.process = None
        self.assertEqual((await self.engine.wake())['state'], 'ready')
        await self.engine.check_idle()
        self.process.terminate.assert_not_called()

    async def test_concurrent_wake_starts_only_one_process(self):
        self.engine.process = None
        self.engine.probe = AsyncMock(side_effect=aiohttp.ClientConnectionError())
        with patch('scripts.engine_supervisor.asyncio.create_subprocess_exec', AsyncMock(return_value=self.process)) as spawn:
            results = await asyncio.gather(self.engine.wake(), self.engine.wake(), self.engine.wake())
        spawn.assert_awaited_once()
        self.assertTrue(all(result['state'] == 'starting' for result in results))

    async def test_dashboard_does_not_wake_sleeping_engine(self):
        self.engine.process = None
        self.engine.probe = AsyncMock(side_effect=aiohttp.ClientConnectionError())
        with patch('scripts.engine_supervisor.asyncio.create_subprocess_exec', AsyncMock()) as spawn:
            status = await self.engine.status()
        spawn.assert_not_called()
        self.assertEqual(status['state'], 'sleeping')
        self.assertGreater(status['memory_total'], 0)

    async def test_startup_timeout_stops_child_and_reports_error(self):
        self.engine.started = time.monotonic() - 121
        self.engine.probe = AsyncMock(side_effect=aiohttp.ClientConnectionError())
        await self.engine.check_idle()
        self.process.terminate.assert_called_once()
        with self.assertRaisesRegex(RuntimeError, 'could not start'):
            await self.engine.wake()

    async def test_unresponsive_engine_after_startup_is_never_killed(self):
        await self.engine.status()
        self.engine.probe = AsyncMock(side_effect=asyncio.TimeoutError())
        await self.engine.check_idle()
        self.process.terminate.assert_not_called()

    async def test_wrong_project_is_never_reused(self):
        self.engine.probe = AsyncMock(return_value={'system': {'argv': ['another-project']}})
        with self.assertRaisesRegex(RuntimeError, 'Another image engine'):
            await self.engine.wake()
        await self.engine.check_idle()
        self.process.terminate.assert_not_called()


class NativeClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_is_passive_and_wake_is_authenticated_post(self):
        requests = []
        async def handler(request):
            requests.append((request.method, request.path, request.headers.get('Authorization')))
            return web.json_response({'state': 'starting' if request.method == 'POST' else 'sleeping'})
        app = web.Application()
        app.router.add_get('/engine', handler)
        app.router.add_post('/engine/wake', handler)
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / 'token'
            token.write_text('test-secret')
            async with TestServer(app) as server:
                client = NativeEngine()
                client.base = str(server.make_url('')).rstrip('/')
                client.token_file = token
                self.assertEqual((await client.request())['state'], 'sleeping')
                self.assertEqual((await client.request(wake=True))['state'], 'starting')
        self.assertEqual(requests, [('GET', '/engine', 'Bearer test-secret'), ('POST', '/engine/wake', 'Bearer test-secret')])
