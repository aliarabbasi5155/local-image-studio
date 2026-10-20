import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from studio.tests import test_studio as fixtures

photo = fixtures.photo


class SharingTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.APITests.asyncSetUp
    asyncTearDown = fixtures.APITests.asyncTearDown

    async def test_shortcut_upload_needs_pin_even_with_browser_session(self):
        await self.client.post('/api/login', json={'pin': '123456'})
        for headers in ({}, {'X-Studio-PIN': 'wrong'}):
            response = await self.client.post('/api/share-upload', data=photo(), headers=headers)
            self.assertEqual(response.status, 401)
        self.assertFalse(self.store.rows('SELECT * FROM uploads'))

    async def test_shortcut_saves_project_and_returns_link_without_unlocking_browser(self):
        response = await self.client.post('/api/share-upload', data=photo(), headers={'X-Studio-PIN': '123456'})
        self.assertEqual(response.status, 201)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        result = await response.json()
        link = urlsplit(result['url'])
        self.assertEqual(link.netloc, self.client.make_url('/').host + ':' + str(self.client.port))
        self.assertEqual(parse_qs(link.fragment), {'project': [result['project_id']], 'image': [result['id']]})
        self.assertFalse(response.cookies)
        self.assertEqual((await self.client.get('/api/projects')).status, 401)
        await self.client.post('/api/login', json={'pin': '123456'})
        project = await (await self.client.get('/api/projects/' + result['project_id'])).json()
        self.assertEqual(project['versions'][0]['id'], result['id'])
        self.assertTrue(project['versions'][0]['ready'])
        self.assertFalse(self.store.rows('SELECT * FROM jobs'))

    async def test_shortcut_rejects_invalid_oversized_and_unsupported_images(self):
        for data in (b'', b'not a photo', photo(32, 32)):
            response = await self.client.post('/api/share-upload', data=data, headers={'X-Studio-PIN': '123456'})
            self.assertEqual(response.status, 400)
        with patch('studio.server.MAX_UPLOAD', 20):
            response = await self.client.post('/api/share-upload', data=photo(), headers={'X-Studio-PIN': '123456'})
            self.assertEqual(response.status, 413)
        self.assertFalse(self.store.rows('SELECT * FROM uploads'))

    async def test_shortcut_pin_attempts_share_login_rate_limit(self):
        for _ in range(4):
            self.assertEqual((await self.client.post('/api/login', json={'pin': 'bad'})).status, 401)
            self.assertEqual((await self.client.post('/api/share-upload', data=photo(), headers={'X-Studio-PIN': 'bad'})).status, 401)
        for path in ('/api/share-upload', '/api/login'):
            response = await self.client.post(path, json={'pin': '123456'}, headers={'X-Studio-PIN': '123456'})
            self.assertEqual(response.status, 429)

    async def test_shortcut_rejects_cross_origin_and_insecure_phone_requests(self):
        for extra in ({'Origin': 'https://unrelated.example'}, {'Origin': 'null'}, {'Host': '192.168.1.20:7878'}):
            response = await self.client.post('/api/share-upload', data=photo(), headers={'X-Studio-PIN': '123456', **extra})
            self.assertEqual(response.status, 403)
        self.assertFalse(self.store.rows('SELECT * FROM uploads'))

    async def test_share_target_requires_worker_and_never_uploads_without_auth(self):
        manifest = await (await self.client.get('/static/manifest.json')).json()
        target = manifest['share_target']
        self.assertEqual((target['method'], target['enctype']), ('POST', 'multipart/form-data'))
        response = await self.client.post(target['action'], data=photo(), allow_redirects=False)
        self.assertEqual(response.status, 303)
        self.assertEqual(response.headers['Location'], '/?share-error=setup')
        self.assertFalse(self.store.rows('SELECT * FROM uploads'))
        guide = await self.client.get('/static/share-help.html')
        self.assertEqual(guide.status, 200)
        self.assertIn('X-Studio-PIN', await guide.text())
