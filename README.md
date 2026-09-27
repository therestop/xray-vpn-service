# Личный VPN: Xray, Telegram и HTTPS-подписки

Рабочая основа для владельца и знакомых: VLESS REALITY TCP + XHTTP, отдельные ключи пользователей, приглашения через Telegram, подписки для Happ/Hiddify/v2rayNG и FlClash. Python 3.12+, стандартная библиотека для работающего сервиса; `cryptography` нужна только при подготовке конфигурации. Оплаты и коммерческий биллинг пока не реализованы.

**В репозитории нет рабочих ключей, токенов, IP конкретного сервера и подписок.** Их нужно генерировать на своём сервере. Резервные копии секретов храните отдельно от GitHub.

## Что работает

- Владелец привязывается по случайному одноразовому коду, только в личном чате.
- `/invite 30` создаёт одноразовое приглашение на 30 дней; активировать нужно за 7 дней.
- У каждого Telegram-пользователя свои UUID и случайная ссылка подписки.
- `/my` выдаёт два URL: базовую подписку и JSON/YAML-профиль Mihomo для FlClash.
- `/vless` выдаёт прямые VLESS-ссылки. Владелец может получать свои прежние профили: администратор помещает точные строки в массив `owner_legacy_links` в закрытом `/etc/vpn-service/app.json` и перезапускает бота. Эти строки доступны только привязанному владельцу в личном чате; знакомые получают собственные ключи. В Git реальные ссылки не сохраняются. Прежние ручные профили не зависят от срока подписки бота.
- `/users`, `/extend ID 30`, `/revoke ID` доступны только владельцу.
- Отзыв меняет UUID и токен, прежние ссылки больше не действуют даже после продления.
- Истечение срока убирает пользователя из Xray при следующем проходе (обычно до минуты, при сбоях применения дольше). HTTP-подписка перестаёт выдаваться сразу после истечения срока.
- Ранее заведённые вручную профили сохраняются: бот управляет только записями с `email=bot-...` в двух своих inbound.
- Перед применением запускается проверка Xray; при неудачном рестарте восстанавливается предыдущая конфигурация.

Это MVP на одном сервере. При выдаче/отзыве/истечении доступа Xray перезапускается: текущие соединения могут кратковременно прерываться. Нет лимита устройств, учёта трафика, оплаты, HA и многосерверного балансирования. `/users` показывает первые 60 записей; это инструмент небольшой закрытой группы. HTTP-обработчик доступен только локально за Nginx. Сервис работает от root для изменения Xray и рестарта systemd, с ограничением записи каталогами конфигурации и состояния.

## Перед установкой

Нужен отдельный VPS с Ubuntu 24.04 amd64, публичным IPv4, доступом root по SSH, исходящим HTTPS и открытыми TCP-портами:

| Порт | Для чего |
|---|---|
| 22 | SSH; замените в firewall, если у вас другой |
| 443 | VLESS TCP REALITY |
| 8443 | VLESS XHTTP REALITY |
| 80 | Проверка владения IP для Let's Encrypt |
| 9443 | HTTPS-подписки |
| 9080 | Только 127.0.0.1, наружу не открывать |

Практический старт — 1 vCPU, 2 ГБ RAM, 20–30 ГБ диска. Пропускная способность зависит от тарифа провайдера и нагрузки, число клиентов заранее не гарантируется. Ни один этот конфиг не гарантирует обход режима «белых списков»: результат проверяется в конкретной сети во время ограничения. Значок VPN не доказывает передачу трафика.

Создайте бота через [официальный BotFather](https://t.me/BotFather), `/newbot`. Используйте отдельного бота, которого не обслуживает другой процесс. Токен не вводится в аргументы команд и не попадает в Git. Для API используется [long polling Telegram](https://core.telegram.org/bots/api#getupdates), поэтому публичный webhook не нужен.

## Установка с нуля

Ниже команды **на VPS под root**, для нового сервера. Не запускайте замену Xray/Nginx поверх рабочей системы без раздела о миграции. В примере `192.0.2.10` — адрес документации: замените своим публичным IPv4. Клонируйте этот репозиторий в `/opt/vpn-service` (для private GitHub используйте доступный вам способ авторизации, не вставляйте PAT в URL).

```bash
apt-get update
apt-get install -y curl unzip nginx python3 python3-venv python3-cryptography ufw git
cd /opt/vpn-service
```

Установите Xray официальным скриптом. Сначала загрузите и просмотрите его; проверенная версия ядра — 26.3.27. Ссылки: [официальный установщик](https://github.com/XTLS/Xray-install), [REALITY](https://xtls.github.io/en/config/transports/reality.html), [XHTTP](https://xtls.github.io/en/config/transports/xhttp.html). Сам installer в примере берётся из upstream main, поэтому просматривайте его при каждой новой установке; версия ядра фиксируется отдельно.

```bash
curl -fL https://raw.githubusercontent.com/XTLS/Xray-install/main/install-release.sh -o /root/install-xray.sh
less /root/install-xray.sh
bash /root/install-xray.sh install --version 26.3.27
id xray >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin xray
install -d -m 755 /etc/systemd/system/xray.service.d
cat > /etc/systemd/system/xray.service.d/20-security.conf <<'EOF'
[Service]
User=xray
Group=xray
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
EOF
```

Создайте секреты (будет скрытый ввод токена бота). На повторном запуске генератор откажется перезаписывать существующий `generated/`. TCP и XHTTP используют общую REALITY-пару, разные порты; у XHTTP нет `flow`. Сначала в конфиге нет пользователей: владелец появится после привязки в Telegram.

```bash
cd /opt/vpn-service
python3 provision.py --server 192.0.2.10
install -d -m 700 /etc/vpn-service /var/lib/vpn-service
install -m 600 generated/app.json /etc/vpn-service/app.json
install -o root -g xray -m 640 generated/xray.json /usr/local/etc/xray/config.json
/usr/local/bin/xray run -test -config /usr/local/etc/xray/config.json
install -m 644 deploy/vpn-service.service /etc/systemd/system/
```

Подготовьте HTTP для выдачи сертификата. На чистом VPS отключите стандартный nginx default; если там уже есть сайты, настройте server block вручную.

```bash
install -d -m 755 /var/lib/letsencrypt
install -m 644 generated/nginx-http.conf /etc/nginx/sites-available/vpn-subscriptions
ln -sfn /etc/nginx/sites-available/vpn-subscriptions /etc/nginx/sites-enabled/vpn-subscriptions
unlink /etc/nginx/sites-enabled/default 2>/dev/null || true
nginx -t && systemctl reload nginx
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 8443/tcp
ufw allow 9443/tcp
ufw default deny incoming
ufw default allow outgoing
ufw enable
```

Если SSH работает не на 22, **сначала** разрешите свой порт. Не закрывайте текущую сессию до проверки второго подключения.

HTTPS здесь использует сертификат **на IPv4 без домена**, с коротким сроком действия около 6 дней. Нужен Certbot с поддержкой IP; проверена версия 5.8.0. Автопродление обязательно. [Инструкция Let's Encrypt](https://letsencrypt.org/2026/03/11/shorter-certs-certbot/).

```bash
python3 -m venv /opt/certbot
/opt/certbot/bin/pip install 'certbot==5.8.0'
/opt/certbot/bin/certbot certonly --webroot -w /var/lib/letsencrypt \
  --ip-address 192.0.2.10 --preferred-profile shortlived \
  --cert-name vpn-subscriptions --agree-tos --register-unsafely-without-email \
  --non-interactive --deploy-hook 'systemctl reload nginx'
install -m 644 generated/nginx-full.conf /etc/nginx/sites-available/vpn-subscriptions
nginx -t && systemctl reload nginx
install -m 644 deploy/vpn-cert-renew.service deploy/vpn-cert-renew.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now xray nginx vpn-service vpn-cert-renew.timer
systemctl restart xray
/opt/certbot/bin/certbot renew --dry-run --run-deploy-hooks --no-random-sleep-on-renew
systemctl is-active xray nginx vpn-service vpn-cert-renew.timer
```

При неудачной выдаче сертификата **остановитесь** и исправьте доступ к HTTP80/DNS/исходящему HTTPS; не включайте TLS-конфиг без файлов сертификата и не отключайте проверку TLS в клиентах.

Откройте ссылку из `generated/owner-link.txt` в своём Telegram, нажмите «Запустить». Бот ответит «Владелец привязан». После привязки повторное применение кода не передаёт права другому человеку. Владелец получает 365 дней, которые можно продлить `/extend СВОЙ_ID 365` (ID виден в `/users`). Токен привязки храните как секрет.

## Миграция существующего Xray

1. Скопируйте `/usr/local/etc/xray/config.json`, Nginx, ключи и БД в закрытый backup вне репозитория.
2. Нужны inbound с тегами `vless-reality` и `vless-xhttp`, одинаковыми REALITY-ключом/SNI/shortIds. Генератор проверяет эти поля.
3. Выполните `python3 provision.py --server ВАШ_IP --adopt /usr/local/etc/xray/config.json`.
4. Установите только `app.json`, `service.py` и systemd unit. **Не заменяйте рабочий Xray сгенерированным файлом:** генератор нужен для получения параметров подписки, а сервис сохраняет ручных пользователей.
5. В существующий HTTPS server Nginx добавьте `location /u/` из `provision.py`, сохранив прежние `/s/...` подписки. Проверьте `nginx -t`, затем reload.
6. Для обновления подписок через VPN добавьте перед запрещающими правилами Xray разрешение `ВАШ_IP/32`, port `9443`, outbound `direct`. Остальные подключения к собственному серверу можно оставить закрытыми.
7. Запустите бота; сверьте работоспособность прежнего профиля и новых подписок. Не запускайте два polling-процесса для одного токена.

При миграции текущие ручные профили не получают автоматический срок действия. `/revoke` управляет только пользователями, выданными ботом.

## Подключение устройств

**Happ / v2rayNG (Android):** `/my` → скопировать первую HTTPS-ссылку → добавить подписку по URL → обновить → выбрать `Skachkov VPN | 🇳🇱 Нидерланды | Основной`. TCP `Skachkov VPN | 🇳🇱 Нидерланды | Резервный` — второй вариант. Поддержка XHTTP требует актуального клиента.

**Hiddify на Android TV / Google TV:** добавить профиль по ссылке, вставить первый URL из `/my`, обновить подписку, выбрать профиль и подключиться. Способ ввода зависит от версии ТВ-приложения: клавиатура/пульт телефона или его функция импорта. На Tizen/webOS нельзя рассчитывать на запуск Android-приложения — понадобится совместимая приставка или VPN на роутере. Сама реальная модель ТВ должна быть проверена отдельно.

**FlClash:** импорт URL, оканчивающегося `/clash`; он содержит JSON, совместимый с YAML. Ядро должно поддерживать XHTTP (проверен Mihomo 1.19.31). Выбрать группу Skachkov VPN / Нидерланды / Основной, режим **Rule**. При необходимости включить TUN. В Rule есть исключение собственного IP: SSH и подписки идут напрямую, иначе управление сервером может уйти через него же и перестать работать. В Global исключения не действуют.

Проверяйте открытие сайтов и внешний IP. Отдельно проведите тест с Wi-Fi и с мобильной сетью, затем в момент фактических «белых списков». Не публикуйте URL, QR и VLESS-ссылки: это пароли доступа. На одном устройстве не включайте одновременно конкурирующие TUN/VPN-клиенты.

## Обслуживание и резервное копирование

```bash
systemctl status vpn-service xray nginx vpn-cert-renew.timer
journalctl -u vpn-service -n 50 --no-pager
journalctl -u xray -n 50 --no-pager
systemctl list-timers vpn-cert-renew.timer
```

В журналах бота выводятся только типы ошибок, без URL Telegram с токеном. Доступы к подпискам Nginx не логирует. `HTTPError` может означать неверный токен, конфликт другого poller (409), лимит Telegram или временный отказ: проверьте состояние бота безопасным вызовом API без публикации ответа/токена.

Храните резервные копии вне проекта, с правами 600, желательно зашифрованными. Бэкап нужен не только для Git: Git содержит код, но не действующие доступы.

```bash
install -d -m 700 /root/vpn-backups
systemctl stop vpn-service
umask 077
tar -czf /root/vpn-backups/vpn-$(date -u +%Y%m%dT%H%M%SZ).tar.gz \
  /etc/vpn-service /var/lib/vpn-service /usr/local/etc/xray \
  /etc/nginx/sites-available/vpn-subscriptions /etc/letsencrypt \
  /etc/systemd/system/vpn-service.service /etc/systemd/system/vpn-cert-renew.service \
  /etc/systemd/system/vpn-cert-renew.timer /etc/systemd/system/xray.service.d
systemctl start vpn-service
```

Остановка бота не останавливает Xray, но выдача подписок и проверка срока временно приостанавливаются. Чтобы восстановиться: остановить бота, распаковать доверенный backup с сохранением прав, проверить `xray run -test`, `nginx -t`, затем `systemctl daemon-reload` и перезапустить службы. При переносе на **другой IP** обновить app.json, routing и Nginx, выдать новый сертификат и новые адреса подписок; старый URL сам не перенаправится.

При обновлении кода: сделать backup, заменить `service.py`, прогнать тесты, `systemctl restart vpn-service`. Не запускайте генерацию заново и не заменяйте БД. `xray-last-good.json` в каталоге состояния — аварийная предыдущая конфигурация; это не полный backup. Если пришлось вручную откатывать Xray, сначала остановите бота, иначе он вновь согласует его с БД.

Для усиления SSH: установите свой публичный ключ, проверьте **вторую** SSH-сессию по ключу и только после этого отключайте вход по паролю. Проект намеренно не меняет SSH автоматически. Включите обновления безопасности ОС по своей политике.

## Проверка кода и границы проверенного

```bash
python3 -m unittest discover -s tests -v
```

Тесты проверяют привязку владельца, изоляцию команд, одноразовость приглашений, истечение доступа, отзыв с ротацией, сохранение ручных клиентов и форматы экспорта. Это не эмуляция мобильного оператора и не тест всех моделей ТВ. Интеграционные проверки на VPS описаны в `docs/VALIDATION.md`.

## Дальнейшая коммерция

Нужны отдельный учёт заказов, идемпотентные подтверждённые платежи, тарифы/лимиты, резервное копирование вне VPS и контроль продления сертификата. Интеграция Lava пока отсутствует. Для цифровых услуг внутри Telegram действуют [правила Telegram Stars](https://core.telegram.org/bots/payments-stars); выбор внешней продажи через Lava нужно проектировать отдельно с учётом её условий и доступности VPN-категории. Нельзя выдавать доступ только по переходу на success URL — требуется проверенное подтверждение оплаты.

## Меню и название профилей

Название сервиса — **Skachkov VPN**. `/menu` или `/start` открывает постоянную клавиатуру с подключением, прямыми ссылками, статусом, локациями и инструкцией. Владелец видит дополнительные кнопки приглашений и управления. Права проверяются на сервере, включая команды, отправленные вручную.

Для настройки имени бота и системной кнопки меню Telegram выполните на VPS:

```bash
python3 /opt/vpn-service/configure_telegram.py /etc/vpn-service/app.json
```

Страна задаётся полем `location_label` в закрытом `app.json` (по умолчанию `🇳🇱 Нидерланды`; при переносе в другую страну измените его). В профилях отображаются бренд, флаг, русское название страны и основной/резервный вариант. Заголовок HTTP `profile-title` задаёт название подписки в поддерживающих его клиентах. FlClash получает группу `Skachkov VPN`.

После изменения оформления обновите подписку в приложении. Название уже импортированной одиночной VLESS-ссылки автоматически не меняется: повторно скопируйте её из бота и импортируйте или переименуйте профиль вручную. Клиент может сохранять выбранное пользователем имя подписки; при необходимости задайте `Skachkov VPN` вручную. Старые ключи сохраняются — меняется только подпись ссылки после `#`. Статические подписки, созданные отдельно от бота, нужно переименовать отдельно, сохраняя их URL и ключи.
