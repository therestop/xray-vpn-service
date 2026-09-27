"""Private, invite-only Telegram provisioning and subscriptions. Python 3.12+."""
import base64
import copy
import datetime
import http.server
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import threading
import time
import urllib.request
import urllib.parse
import uuid


def render_subscription(settings, user, clash=False):
    """Render credentials for the two supported inbound transports."""
    proxies, links = [], []
    for network, port in [('xhttp', settings['xhttp_port']), ('tcp', settings['tcp_port'])]:
        name = 'NL-XHTTP' if network == 'xhttp' else 'NL-Reality'
        params = dict(encryption='none', security='reality', sni=settings['sni'],
                      fp='chrome', pbk=settings['public_key'], sid=settings['short_id'], type=network)
        proxy = dict(name=name, type='vless', server=settings['server'], port=port,
                     uuid=user['uuid'], tls=True, udp=True, servername=settings['sni'],
                     network=network, **{'client-fingerprint': 'chrome', 'reality-opts': {
                         'public-key': settings['public_key'], 'short-id': settings['short_id']}})
        if network == 'xhttp':
            params.update(path=settings['xhttp_path'], mode='stream-one')
            proxy['xhttp-opts'] = dict(path=settings['xhttp_path'], mode='stream-one')
            proxy['alpn'] = ['h2']
        else:
            params['flow'] = proxy['flow'] = 'xtls-rprx-vision'
        links.append(f"vless://{user['uuid']}@{settings['server']}:{port}?{urllib.parse.urlencode(params)}#{name}")
        proxies.append(proxy)
    if not clash:
        return base64.b64encode(('\n'.join(links) + '\n').encode())
    # JSON is also valid YAML; no YAML dependency is needed.
    config = {'mixed-port': 7890, 'allow-lan': False, 'mode': 'rule', 'ipv6': False,
              'log-level': 'warning', 'proxies': proxies,
              'proxy-groups': [{'name': 'VPN', 'type': 'select', 'proxies': [p['name'] for p in proxies]}],
              'dns': {'enable': True, 'enhanced-mode': 'fake-ip', 'fake-ip-range': '198.18.0.1/16',
                      'nameserver': ['https://1.1.1.1/dns-query#VPN']},
              'rules': [f"IP-CIDR,{settings['server']}/32,DIRECT,no-resolve",
                        'IP-CIDR,10.0.0.0/8,DIRECT,no-resolve', 'IP-CIDR,172.16.0.0/12,DIRECT,no-resolve',
                        'IP-CIDR,192.168.0.0/16,DIRECT,no-resolve', 'MATCH,VPN']}
    return json.dumps(config, indent=2).encode()


def reconciled_config(config, users):
    result = copy.deepcopy(config)
    for inbound in result['inbounds']:
        if inbound.get('tag') not in ('vless-reality', 'vless-xhttp'):
            continue
        clients = inbound['settings']['clients']
        clients[:] = [u for u in clients if not u.get('email', '').startswith('bot-')]
        for user in sorted(users, key=lambda u: u['id']):
            client = {'id': user['uuid'], 'email': f"bot-{user['id']}"}
            if inbound['streamSettings']['network'] in ('tcp', 'raw'):
                client['flow'] = 'xtls-rprx-vision'
            clients.append(client)
    return result


class Service:
    def __init__(self, settings):
        self.settings = settings
        self.lock = threading.RLock()
        self.db = sqlite3.connect(settings['database'], check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, uuid TEXT NOT NULL,
            token TEXT UNIQUE NOT NULL, expires INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS invites (code TEXT PRIMARY KEY, days INTEGER NOT NULL,
            expires INTEGER NOT NULL);
        ''')

    def meta(self, key, default=''):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return row[0] if row else default

    def setmeta(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, str(value)))

    def api(self, method, **data):
        request = urllib.request.Request('https://api.telegram.org/bot' + self.settings['bot_token'] + '/' + method,
                                         data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=40) as response:
            payload = json.load(response)
        if not payload.get('ok'):
            raise RuntimeError('Telegram API rejected request')
        return payload['result']

    def sync(self):
        """Validate before restart; restore original bytes if activation fails."""
        path = Path(self.settings['xray_config'])
        original = path.read_bytes()
        config = json.loads(original)
        users = self.db.execute('SELECT * FROM users WHERE expires>?', (int(time.time()),)).fetchall()
        updated = reconciled_config(config, users)
        if updated == config:
            return
        staged = path.with_suffix('.staged.json')
        staged.write_text(json.dumps(updated, indent=2) + '\n')
        os.chmod(staged, 0o640)
        stat = path.stat()
        os.chown(staged, stat.st_uid, stat.st_gid)
        try:
            subprocess.run(['/usr/local/bin/xray', 'run', '-test', '-config', str(staged)],
                           check=True, capture_output=True, timeout=20)
            # Private backup: only the last valid config, outside the repository.
            backup = Path(self.settings['database']).parent / 'xray-last-good.json'
            backup.write_bytes(original)
            os.chmod(backup, 0o600)
            os.replace(staged, path)
            try:
                subprocess.run(['systemctl', 'restart', 'xray'], check=True, capture_output=True, timeout=30)
                subprocess.run(['systemctl', 'is-active', '--quiet', 'xray'], check=True, timeout=10)
            except Exception:
                path.write_bytes(original)
                subprocess.run(['systemctl', 'restart', 'xray'], check=True, capture_output=True, timeout=30)
                raise
        finally:
            staged.unlink(missing_ok=True)

    def links(self, user):
        base = self.settings['subscription_base'] + '/u/' + user['token']
        until = datetime.datetime.fromtimestamp(user['expires'], datetime.timezone.utc).strftime('%d.%m.%Y %H:%M UTC')
        return ('Доступ до ' + until + '\n\nHapp / Hiddify / v2rayNG:\n' + base +
                '\n\nFlClash (импорт по URL, режим Rule):\n' + base + '/clash' +
                '\n\n/vless — прямые VLESS-ссылки (для владельца — также прежние личные профили).'
                '\n\nСсылки личные: не публикуйте их. Сначала попробуйте профиль NL-XHTTP.')

    def handle(self, message, update_id=None):
        with self.lock, self.db:
            if update_id is not None and update_id < int(self.meta('offset', '0')):
                return None
            response = self._handle(message)
            if update_id is not None:
                self.setmeta('offset', update_id + 1)
            return response

    def _handle(self, message):
        if message.get('chat', {}).get('type') != 'private' or message.get('from', {}).get('is_bot'):
            return None
        uid = message.get('from', {}).get('id')
        if not isinstance(uid, int) or message['chat'].get('id') != uid:
            return None
        parts = message.get('text', '').split()
        command = parts[0].split('@')[0] if parts else ''
        args = parts[1:]
        with self.lock:
            owner = self.meta('owner_id')
            if command == '/start' and len(args) == 1 and not owner:
                if secrets.compare_digest(args[0], self.settings['bootstrap_code']):
                    self.setmeta('owner_id', uid)
                    self.db.execute('INSERT OR IGNORE INTO users VALUES (?,?,?,?)',
                                    (uid, str(uuid.uuid4()), secrets.token_urlsafe(32), int(time.time()) + 365 * 86400))
                    self.sync()
                    return 'Владелец привязан. /my — подписка; /help — управление.'
            if command == '/start' and len(args) == 1:
                invitation = self.db.execute('SELECT * FROM invites WHERE code=? AND expires>?',
                                            (args[0], int(time.time()))).fetchone()
                if invitation:
                    user = self.db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
                    expiry = max(int(time.time()), user['expires'] if user else 0) + invitation['days'] * 86400
                    if user:
                        self.db.execute('UPDATE users SET expires=? WHERE id=?', (expiry, uid))
                    else:
                        self.db.execute('INSERT INTO users VALUES (?,?,?,?)',
                                        (uid, str(uuid.uuid4()), secrets.token_urlsafe(32), expiry))
                    self.db.execute('DELETE FROM invites WHERE code=?', (args[0],))
                    self.sync()
                    return self.links(self.db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone())
            user = self.db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
            admin = owner == str(uid)
            if command == '/vless':
                if admin and self.settings.get('owner_legacy_links'):
                    return 'Прежние личные VLESS-ссылки владельца:\n\n' + '\n\n'.join(self.settings['owner_legacy_links'])
                if user and user['expires'] > time.time():
                    self.sync()
                    return 'Ваши личные VLESS-ссылки:\n\n' + base64.b64decode(render_subscription(self.settings, user)).decode()
                return 'Нет активного доступа. Попросите владельца выдать приглашение.'
            if command in ('/start', '/my', '/status'):
                if user and user['expires'] > time.time():
                    self.sync()
                    return self.links(user)
                return 'Нет активного доступа. Попросите владельца выдать приглашение.'
            if admin:
                if command == '/invite' and len(args) == 1:
                    days = int(args[0])
                    if not 1 <= days <= 3650:
                        return 'Укажите от 1 до 3650 дней.'
                    token = secrets.token_urlsafe(24)
                    self.db.execute('INSERT INTO invites VALUES (?,?,?)', (token, days, int(time.time()) + 7 * 86400))
                    return f"Одноразовое приглашение на {days} дней (активация в течение 7 дней):\nhttps://t.me/{self.settings['bot_username']}?start={token}"
                if command == '/users':
                    rows = self.db.execute('SELECT id,expires FROM users ORDER BY id LIMIT 60').fetchall()
                    return '\n'.join(f"{r['id']}: {datetime.datetime.fromtimestamp(r['expires'], datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}" for r in rows) or 'Пользователей нет.'
                if command in ('/revoke', '/extend') and len(args) == (1 if command == '/revoke' else 2):
                    target = int(args[0])
                    row = self.db.execute('SELECT * FROM users WHERE id=?', (target,)).fetchone()
                    if not row:
                        return 'Пользователь не найден.'
                    if command == '/revoke':
                        self.db.execute('UPDATE users SET expires=0,uuid=?,token=? WHERE id=?',
                                        (str(uuid.uuid4()), secrets.token_urlsafe(32), target))
                    else:
                        days = int(args[1])
                        if not 1 <= days <= 3650:
                            return 'Укажите от 1 до 3650 дней.'
                        self.db.execute('UPDATE users SET expires=? WHERE id=?',
                                        (max(int(time.time()), row['expires']) + days * 86400, target))
                    self.sync()
                    return 'Готово. Пользователь может получить актуальную ссылку командой /my.'
                return '/my — подписка\n/vless — прежние личные VLESS-ссылки\n/invite 30 — приглашение на 30 дней\n/users — ID и сроки\n/extend ID 30 — продлить\n/revoke ID — отозвать и сменить ключи'
            return '/my — ваша подписка\n/vless — ваши прямые ссылки\n/status — срок действия. Доступ выдаёт владелец.'

    def subscription(self, token, clash=False):
        with self.lock:
            row = self.db.execute('SELECT * FROM users WHERE token=? AND expires>?',
                                  (token, int(time.time()))).fetchone()
            return render_subscription(self.settings, row, clash) if row else None

    def serve(self):
        service = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass  # URL contains a credential.

            def do_GET(self):
                parts = urllib.parse.urlsplit(self.path).path.strip('/').split('/')
                content = None
                if len(parts) in (2, 3) and parts[0] == 'u' and (len(parts) == 2 or parts[2] == 'clash'):
                    content = service.subscription(parts[1], len(parts) == 3)
                if content is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('profile-update-interval', '6')
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 9080), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        while True:
            try:
                with self.lock:
                    self.sync()  # Expiry remains enforced even when Telegram is unreachable.
                    offset = int(self.meta('offset', '0'))
                updates = self.api('getUpdates', offset=offset, timeout=20, allowed_updates=['message'])
                for update in updates:
                    message = update.get('message', {})
                    try:
                        response = self.handle(message, update['update_id'])
                    except ValueError:
                        response = 'Неверные аргументы. Используйте /help.'
                    if response:
                        # Commit handling before sending; failed send cannot repeat provisioning.
                        with self.lock, self.db:
                            self.setmeta('offset', update['update_id'] + 1)
                        try:
                            self.api('sendMessage', chat_id=message['chat']['id'], text=response,
                                     link_preview_options={'is_disabled': True})
                        except Exception as exc:
                            print('Reply failed:', type(exc).__name__, flush=True)
                    else:
                        with self.lock, self.db:
                            self.setmeta('offset', update['update_id'] + 1)
            except Exception as exc:
                # Never print exception text: urllib errors can contain the bot token.
                print('Service retry:', type(exc).__name__, flush=True)
                time.sleep(5)


if __name__ == '__main__':
    import sys
    os.umask(0o077)
    settings = json.loads(Path(sys.argv[1]).read_text())
    Service(settings).serve()
