# Cloudflare Worker для TgWsProxy

Свой Worker обходит ограничение чужих публичных воркеров: медиа не
заталкивается в тело H2-запроса, а идёт потоком через WebSocket к DC
Telegram.

## Зачем

Через публичные CF-домены (`kws*.pclead.co.uk` и т.п.) медиа в Telegram не
грузится:

- H2 принимает только небольшие тела: `POST 16 КБ` → ответ, `POST 64 КБ` →
  таймаут;
- WS-fallback отдаёт 0.5–1 КБ/с.

Свой Worker работает через `cloudflare:sockets`: каждое сообщение WebSocket
сразу уходит в TCP к DC Telegram, без буферизации тела запроса.

## Что нужно

1. Аккаунт Cloudflare с подтверждённой почтой.
2. Node.js и npm (уже есть: v22).
3. Домены `cloudflare.com`, `cloudflare.dev`, `workers.dev` в hostlist
   zapret — они должны обходиться.

## Деплой через wrangler (рекомендуется)

```bash
cd deploy/cf-worker
npx wrangler login          # откроет браузер, войти в Cloudflare
npx wrangler deploy
```

В выводе будет домен:

```
https://tgws-worker.<account>.workers.dev
```

## Деплой через панель Cloudflare

1. `Compute` → `Workers & Pages` → `Create application`.
2. `Start with Hello World!` → `Deploy`.
3. `Edit code` → вставить содержимое `worker.js` → `Deploy`.
4. Скопировать домен вида `random-words-1234.username.workers.dev`.

## Подключение к TgWsProxy

```bash
# домен без https:// и без слэша
linfix tgws config --worker-domain tgws-worker.<account>.workers.dev
linfix tgws restart
```

Или вручную в `~/.config/TgWsProxy/config.json`:

```json
"cfproxy_worker_domain": ["tgws-worker.<account>.workers.dev"],
"cfproxy_worker_enabled": true
```

Проверка в логе:

```
CF proxy worker: enabled (tgws-worker....workers.dev)
... -> trying CF worker ... for 149.154.167.220
```

## Если фото/видео всё ещё не грузятся

В документации upstream: на аккаунте без Premium оставить в `dc_ip` только
DC4:

```json
"dc_ip": ["4:149.154.167.220"]
```

## Ограничения

- Worker на бесплатном тарифе имеет лимиты CPU и числа запросов; для
  личного использования их обычно хватает.
- `workers.dev` может быть недоступен без обхода DPI — поэтому домены
  `workers.dev`/`cloudflare.com`/`cloudflare.dev` добавляются в hostlist
  zapret.
- Домен Worker'а — это не секрет, но и не публикуйте его без необходимости:
  через него можно гонять трафик за ваш счёт.
