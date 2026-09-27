"""Generate a fresh deployment, or adopt an existing compatible Xray config."""
import argparse
import base64
import getpass
import ipaddress
import json
import os
from pathlib import Path
import secrets
import urllib.request
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives import serialization


def encode(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def generate(server, token, config=None):
    ipaddress.IPv4Address(server)
    if config is None:
        key = X25519PrivateKey.generate()
        private = encode(key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                           serialization.NoEncryption()))
        reality = {'show': False, 'target': 'www.apple.com:443', 'xver': 0,
                   'serverNames': ['www.apple.com'], 'privateKey': private, 'shortIds': [secrets.token_hex(8)]}
        config = {'log': {'access': 'none', 'loglevel': 'warning'}, 'inbounds': [],
                  'outbounds': [{'tag': 'direct', 'protocol': 'freedom'}, {'tag': 'block', 'protocol': 'blackhole'}],
                  'routing': {'domainStrategy': 'IPIfNonMatch', 'rules': [
                      {'type': 'field', 'ip': [server + '/32'], 'port': '9443', 'outboundTag': 'direct'},
                      {'type': 'field', 'ip': ['geoip:private', server + '/32'], 'outboundTag': 'block'},
                      {'type': 'field', 'port': '25,465,587', 'outboundTag': 'block'}]}}
        for tag, network, port in [('vless-reality', 'tcp', 443), ('vless-xhttp', 'xhttp', 8443)]:
            stream = {'network': network, 'security': 'reality', 'realitySettings': reality}
            if network == 'xhttp':
                stream['xhttpSettings'] = {'path': '/' + secrets.token_hex(12), 'mode': 'auto'}
            config['inbounds'].append({'tag': tag, 'listen': '0.0.0.0', 'port': port, 'protocol': 'vless',
                                       'settings': {'clients': [], 'decryption': 'none'}, 'streamSettings': stream})
    inbounds = {i['tag']: i for i in config['inbounds']}
    tcp, xhttp = inbounds['vless-reality'], inbounds['vless-xhttp']
    reality = tcp['streamSettings']['realitySettings']
    other = xhttp['streamSettings']['realitySettings']
    for field in ('privateKey', 'serverNames', 'shortIds'):
        if other[field] != reality[field]:
            raise ValueError('Inbound REALITY parameters must match')
    key = X25519PrivateKey.from_private_bytes(base64.urlsafe_b64decode(reality['privateKey'] + '=' * (-len(reality['privateKey']) % 4)))
    public = encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    settings = {'server': server, 'bot_token': token, 'bot_username': '', 'location_label': '🇳🇱 Нидерланды',
                'bootstrap_code': secrets.token_urlsafe(24), 'database': '/var/lib/vpn-service/state.sqlite',
                'xray_config': '/usr/local/etc/xray/config.json', 'subscription_base': f'https://{server}:9443',
                'sni': reality['serverNames'][0], 'public_key': public, 'short_id': reality['shortIds'][0],
                'tcp_port': tcp['port'], 'xhttp_port': xhttp['port'],
                'xhttp_path': xhttp['streamSettings']['xhttpSettings']['path']}
    return config, settings


HTTP = '''server {
    listen 80;
    server_name SERVER;
    access_log off;
    location ^~ /.well-known/acme-challenge/ {
        root /var/lib/letsencrypt;
        default_type text/plain;
        try_files $uri =404;
    }
    location / { return 404; }
}
'''
HTTPS = '''limit_req_zone $binary_remote_addr zone=vpn_subscriptions:1m rate=12r/m;
server {
    listen 9443 ssl;
    server_name SERVER;
    ssl_certificate /etc/letsencrypt/live/vpn-subscriptions/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/vpn-subscriptions/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    server_tokens off;
    access_log off;
    location /u/ {
        limit_req zone=vpn_subscriptions burst=8 nodelay;
        limit_req_status 429;
        proxy_pass http://127.0.0.1:9080;
        proxy_connect_timeout 3s;
        proxy_read_timeout 10s;
        add_header X-Content-Type-Options "nosniff" always;
    }
    location / { return 404; }
}
'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--server', required=True, help='Public IPv4')
    parser.add_argument('--adopt', type=Path, help='Existing compatible Xray JSON; never overwritten here')
    parser.add_argument('--out', type=Path, default=Path('generated'))
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit('Output already exists. Back it up before generating new keys.')
    os.umask(0o077)
    token = getpass.getpass('Telegram bot token (hidden): ').strip()
    config, settings = generate(args.server, token, json.loads(args.adopt.read_text()) if args.adopt else None)
    try:
        with urllib.request.urlopen('https://api.telegram.org/bot' + token + '/getMe', timeout=20) as response:
            settings['bot_username'] = json.load(response)['result']['username']
        with urllib.request.urlopen('https://api.telegram.org/bot' + token + '/getWebhookInfo', timeout=20) as response:
            if json.load(response)['result']['url']:
                raise RuntimeError('Existing webhook: migrate the bot explicitly first')
    except Exception as error:
        raise SystemExit('Bot validation failed (' + type(error).__name__ + '). Token not printed.') from None
    args.out.mkdir(mode=0o700, parents=True)
    for name, data in [('xray.json', config), ('app.json', settings)]:
        (args.out / name).write_text(json.dumps(data, indent=2) + '\n')
    (args.out / 'nginx-http.conf').write_text(HTTP.replace('SERVER', args.server))
    (args.out / 'nginx-full.conf').write_text((HTTP + HTTPS).replace('SERVER', args.server))
    (args.out / 'owner-link.txt').write_text(f"https://t.me/{settings['bot_username']}?start={settings['bootstrap_code']}\n")
    print('Generated private configuration. Owner link: ' + str(args.out / 'owner-link.txt'))
