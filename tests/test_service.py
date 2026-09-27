import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from service import Service, reconciled_config, render_subscription
from provision import generate


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config, self.settings = generate('192.0.2.1', 'test-placeholder')
        self.settings.update(database=str(Path(self.tmp.name) / 'state.sqlite'), bot_username='example_bot')
        self.service = Service(self.settings)
        self.sync = patch.object(self.service, 'sync')
        self.sync.start()

    def tearDown(self):
        self.sync.stop()
        self.service.db.close()
        self.tmp.cleanup()

    def message(self, uid, text, kind='private'):
        return self.service.handle({'from': {'id': uid}, 'chat': {'type': kind, 'id': uid}, 'text': text})

    def owner(self):
        self.message(1, '/start ' + self.settings['bootstrap_code'])

    def test_owner_requires_code_and_private_chat(self):
        self.message(2, '/start wrong')
        self.message(2, '/start ' + self.settings['bootstrap_code'], 'group')
        self.assertEqual(self.service.meta('owner_id'), '')
        self.owner()
        self.message(2, '/start ' + self.settings['bootstrap_code'])
        self.assertEqual(self.service.meta('owner_id'), '1')

    def test_invite_once_and_permissions(self):
        self.owner()
        self.message(2, '/invite 30')
        self.assertEqual(self.service.db.execute('SELECT COUNT(*) FROM invites').fetchone()[0], 0)
        text = self.message(1, '/invite 30')
        code = text.split('start=')[1]
        self.message(2, '/start ' + code)
        self.message(3, '/start ' + code)
        self.assertIsNone(self.service.db.execute('SELECT * FROM users WHERE id=3').fetchone())
        user = self.service.db.execute('SELECT * FROM users WHERE id=2').fetchone()
        self.assertIsNotNone(self.service.subscription(user['token']))
        self.message(3, '/revoke 2')
        self.assertIsNotNone(self.service.subscription(user['token']))
        self.message(1, '/revoke 2')
        self.assertIsNone(self.service.subscription(user['token']))
        self.message(1, '/extend 2 30')
        new = self.service.db.execute('SELECT * FROM users WHERE id=2').fetchone()
        self.assertNotEqual(user['uuid'], new['uuid'])
        self.assertIsNone(self.service.subscription(user['token']))
        self.assertIsNotNone(self.service.subscription(new['token']))

    def test_expired_and_unknown_subscriptions_denied(self):
        self.owner()
        user = self.service.db.execute('SELECT * FROM users').fetchone()
        self.assertIsNone(self.service.subscription('unknown'))
        self.service.db.execute('UPDATE users SET expires=?', (int(time.time()) - 1,))
        self.assertIsNone(self.service.subscription(user['token']))

    def test_preserves_existing_users_and_flow(self):
        self.config['inbounds'][0]['settings']['clients'] = [{'id': 'owner-untouched', 'email': 'owner'}]
        self.config['inbounds'][1]['settings']['clients'] = [{'id': 'old-managed', 'email': 'bot-9'}]
        updated = reconciled_config(self.config, [{'id': 2, 'uuid': 'new-user'}])
        self.assertEqual(updated['inbounds'][0]['settings']['clients'][0]['id'], 'owner-untouched')
        self.assertEqual(updated['inbounds'][0]['settings']['clients'][1]['flow'], 'xtls-rprx-vision')
        self.assertEqual(updated['inbounds'][1]['settings']['clients'], [{'id': 'new-user', 'email': 'bot-2'}])

    def test_client_formats_and_direct_admin_rule(self):
        user = {'uuid': 'test-uuid'}
        text = base64.b64decode(render_subscription(self.settings, user)).decode()
        self.assertEqual(len(text.splitlines()), 2)
        self.assertIn('mode=stream-one', text)
        config = json.loads(render_subscription(self.settings, user, True))
        self.assertEqual(config['rules'][0], 'IP-CIDR,192.0.2.1/32,DIRECT,no-resolve')
        self.assertNotIn('flow', config['proxies'][0])

    def test_adoption_keeps_keys(self):
        config, settings = generate('192.0.2.1', 'another-placeholder', self.config)
        self.assertEqual(settings['public_key'], self.settings['public_key'])
        self.assertEqual(config, self.config)

    def test_legacy_links_are_owner_only(self):
        legacy = 'vless://legacy-test-only@192.0.2.1:443#Original'
        self.settings['owner_legacy_links'] = [legacy]
        self.assertNotIn(legacy, self.message(2, '/vless'))
        self.owner()
        self.assertIn(legacy, self.message(1, '/vless'))
        self.assertIsNone(self.message(1, '/vless', 'group'))
        code = self.message(1, '/invite 30').split('start=')[1]
        self.message(2, '/start ' + code)
        response = self.message(2, '/vless')
        self.assertNotIn(legacy, response)
        user = self.service.db.execute('SELECT * FROM users WHERE id=2').fetchone()
        self.assertIn(user['uuid'], response)
        self.message(1, '/revoke 2')
        self.assertNotIn('vless://', self.message(2, '/vless'))

    def test_owner_without_legacy_receives_own_links(self):
        self.owner()
        response = self.message(1, '/vless')
        self.assertEqual(response.count('vless://'), 2)

    def test_duplicate_update_does_not_extend_twice(self):
        self.owner()
        message = {'from': {'id': 1}, 'chat': {'type': 'private', 'id': 1}, 'text': '/extend 1 30'}
        self.service.handle(message, 500)
        expiry = self.service.db.execute('SELECT expires FROM users WHERE id=1').fetchone()[0]
        self.service.handle(message, 500)
        self.assertEqual(self.service.db.execute('SELECT expires FROM users WHERE id=1').fetchone()[0], expiry)

    def test_failed_provision_rolls_back_database_and_offset(self):
        self.owner()
        code = self.message(1, '/invite 30').split('start=')[1]
        self.service.sync.side_effect = RuntimeError('simulated activation failure')
        with self.assertRaises(RuntimeError):
            self.service.handle({'from': {'id': 2}, 'chat': {'type': 'private', 'id': 2}, 'text': '/start ' + code}, 500)
        self.assertIsNone(self.service.db.execute('SELECT * FROM users WHERE id=2').fetchone())
        self.assertIsNotNone(self.service.db.execute('SELECT * FROM invites WHERE code=?', (code,)).fetchone())
        self.assertEqual(self.service.meta('offset', '0'), '0')


if __name__ == '__main__':
    unittest.main()
