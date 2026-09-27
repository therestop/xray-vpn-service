"""Publish bot name and command menu, without sending any chat messages."""
import json
import sys
from pathlib import Path
from service import BRAND, Service


def configure(service):
    commands = [{'command': command, 'description': description} for command, description in [
        ('menu', 'Главное меню'), ('my', 'Подключить VPN'), ('vless', 'Прямые VLESS-ссылки'),
        ('status', 'Мой доступ'), ('locations', 'Доступные локации'), ('guide', 'Как подключиться')]]
    service.api('setMyName', name=BRAND)
    service.api('setMyName', name=BRAND, language_code='ru')
    service.api('setMyDescription', description='Skachkov VPN — личный доступ к VPN. Подключение телефона, компьютера и ТВ. Вход по приглашению владельца.')
    service.api('setMyShortDescription', short_description='Skachkov VPN · Подписки для телефона, компьютера и ТВ')
    for language in ('', 'ru'):
        service.api('setMyCommands', commands=commands, scope={'type': 'all_private_chats'}, language_code=language)
    service.api('setChatMenuButton', menu_button={'type': 'commands'})


if __name__ == '__main__':
    service = Service(json.loads(Path(sys.argv[1]).read_text()))
    try:
        configure(service)
        assert service.api('getMyName')['name'] == BRAND
        assert service.api('getChatMenuButton')['type'] == 'commands'
        print('Telegram name and command menu configured.')
    except Exception as error:
        raise SystemExit('Telegram configuration failed: ' + type(error).__name__) from None
    finally:
        service.db.close()
