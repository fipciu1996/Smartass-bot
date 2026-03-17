# Smartass SQL Bot

Prosty bot Telegram, ktory korzysta z OpenRouter tool calling do odpytywania
Postgresa i zwracania gotowej odpowiedzi w jezyku nadawcy. Glowny przeplyw jest
liniowy:

1. Telegram `getUpdates`
2. wiadomosc uzytkownika
3. OpenRouter `chat/completions` z narzedziem `run_sql`
4. model wywoluje `run_sql` z jednym read-only SQL na podstawie DDL schematu `gym`
5. lokalny runner wykonuje SQL na PostgreSQL
6. wynik wraca do OpenRouter jako `role: "tool"`
7. model formuluje finalna odpowiedz w jezyku nadawcy

## Zalozenia

- brak lokalnego NLU i mapowania intentow
- HTTP obslugiwane przez standard library, PostgreSQL przez `psycopg`
- konfiguracja wylacznie przez `.env`
- walidacja bezpieczenstwa: tylko pojedynczy `SELECT` albo `WITH`
- finalna odpowiedz generowana przez model po otrzymaniu wyniku z narzedzia
- prompt zawiera dodatkowe definicje KPI i zaleznosci biznesowych wyciagniete z dashboardu Grafany `gym-stats-smartass-codex / gym-statistics-final`

## Konfiguracja

Aktywuj virtualenv i zainstaluj zaleznosc:

```powershell
python -m pip install -r requirements.txt
```

Skopiuj `.env.example` do `.env` i ustaw:

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_GROUP_LISTEN_MODE=mentioned
OPENROUTER_API_KEY=...
OPENROUTER_MODEL=openai/gpt-4o-mini
GYM_DB_HOST=...
GYM_DB_NAME=smartass
GYM_DB_USER=...
GYM_DB_PASSWORD=...
CONTEXT_DB_HOST=127.0.0.1
CONTEXT_DB_NAME=smartass_bot
CONTEXT_DB_USER=smartass_bot
CONTEXT_DB_PASSWORD=change-me
```

Opcjonalnie:

```env
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_HTTP_REFERER=https://smartass-bot.local
OPENROUTER_APP_TITLE=Smartass SQL Bot
TELEGRAM_ALLOWED_CHAT_IDS=
GYM_DB_PORT=5432
GYM_DB_SSLMODE=prefer
GYM_DB_CONNECT_TIMEOUT_SECONDS=10
CONTEXT_DB_PORT=5432
CONTEXT_DB_SSLMODE=disable
CONTEXT_DB_CONNECT_TIMEOUT_SECONDS=10
OPENROUTER_TIMEOUT_SECONDS=60
TELEGRAM_POLL_TIMEOUT_SECONDS=20
RETRY_DELAY_SECONDS=3
CONTEXT_HISTORY_MESSAGES=8
CONTEXT_SUMMARY_TRIGGER_MESSAGES=20
CONTEXT_SUMMARY_KEEP_RECENT_MESSAGES=8
MAX_TOOL_ITERATIONS=6
```

## Uruchomienie

```powershell
python main.py
```

Bot dziala w trybie long-polling, zgodnie z prostym wzorcem z `day1.ipynb`.

## Grupy Telegram

Bot obsluguje teraz czaty `private`, `group` i `supergroup`.

- `TELEGRAM_GROUP_LISTEN_MODE=mentioned`: w grupach odpowiada tylko wtedy, gdy jest wzmiankowany albo gdy ktos odpowiada na jego wiadomosc
- `TELEGRAM_GROUP_LISTEN_MODE=all`: odpowiada na kazda wiadomosc tekstowa widoczna dla bota w grupie
- `TELEGRAM_ALLOWED_CHAT_IDS`: opcjonalna lista dozwolonych grup rozdzielona przecinkami, np. `-1001234567890,-1009876543210`; dla supergrup bot akceptuje tez czesty zapis bez prefiksu `-100`

W grupach bot odpisuje jako reply do konkretnej wiadomosci i zachowuje `message_thread_id`, wiec dobrze dziala tez w watkach tematow.

Jesli chcesz, zeby bot widzial cala komunikacje w grupie, w `@BotFather` trzeba wylaczyc privacy mode. W przeciwnym razie Telegram zwykle dostarczy mu tylko komendy, wzmianki i odpowiedzi na jego wiadomosci.

## Docker

Zbuduj i uruchom serwis bota:

```powershell
docker compose up --build -d
```

Zatrzymanie:

```powershell
docker compose down
```

Compose uruchamia dwa serwisy:

- `bot`: aktualny bot Telegram/OpenRouter
- `context-db`: lokalny PostgreSQL pod przyszle trzymanie kontekstu konwersacji
- `context-db`: lokalny PostgreSQL dla ostatnich tur konwersacji

`context-db` ma trwaly wolumen, healthcheck oraz skrypt inicjalizacyjny, ktory
tworzy schema `bot_context` z tabelami:

- `conversation_session`
- `conversation_message`

`context-db` nie publikuje portu na hosta. Jest podpiety tylko do wewnetrznej
siec `context-net`, do ktorej dolaczony jest rowniez `bot`, wiec dostep do tej
bazy ma tylko kontener bota.

Bot laduje z `context-db` ostatnie `CONTEXT_HISTORY_MESSAGES` wiadomosci
`user/assistant` dla danego `chat_id` i dolacza je do requestu OpenRouter przed
biezaca wiadomoscia uzytkownika.

Po przekroczeniu `CONTEXT_SUMMARY_TRIGGER_MESSAGES` starsze wiadomosci sa
kompresowane do rolling summary w `bot_context.conversation_session.summary`,
a w prompt trafia:

- summary starszej rozmowy
- ostatnie `CONTEXT_HISTORY_MESSAGES` wiadomosci
- biezaca wiadomosc uzytkownika

## Test lokalny

```powershell
python test_integration.py
python -m py_compile main.py test_integration.py
```

## Gdzie jest logika

- `main.py`:
  - loader `.env`
  - klient Telegram
  - klient OpenRouter
  - loader promptow z katalogu `prompts/`
  - schema narzedzia `run_sql`
  - wykonanie SQL przez `psycopg`
  - petla tool-calling i finalna odpowiedz do uzytkownika
- `prompts/`: osobne pliki z promptem systemowym, summary promptem i opisem schematu/analityki `gym`
- `test_integration.py`: lekkie smoke testy bez wywolan sieciowych
- `requirements.txt`: minimalna zaleznosc runtime dla Postgresa
- `docker/postgres/init/01-context-schema.sql`: bootstrap schematu konwersacji
