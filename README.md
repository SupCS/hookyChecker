# Hooky Checker

MVP системы мониторинга качества reporting-источника. Система ежедневно
сохраняет состояние данных, сравнивает его с предыдущим успешным снимком и
показывает активные и исторические инциденты.

## Предлагаемый стек

- Python 3.12 — один язык для загрузки, проверок и UI.
- FastAPI + Jinja — UI и webhook в одном web-приложении.
- PostgreSQL 16 — snapshots, результаты проверок и жизненный цикл алертов.
- SQLAlchemy 2 + Alembic — модели данных и миграции.
- Pandas + PyArrow — чтение и агрегация данных.
- Google Sheets CSV/API — единственный источник данных MVP.
- Pydantic Settings — конфигурация и secrets через environment variables.
- Pytest — unit- и integration-тесты.
- Ruff + mypy — linting, formatting и базовая проверка типов.
- Railway:
  - один Web service запускает UI и ingestion API;
  - PostgreSQL service хранит состояние;
  - Cron service один раз в день запускает ingestion/check pipeline.

Server-rendered UI выбран для простого deployment: пользователю и Railway нужен
один процесс и один порт. Если продукт станет внешним или multi-tenant,
интерфейс можно заменить на Next.js, сохранив API, схему БД и pipeline.

## Архитектура MVP

```text
Google Sheets
          |
     Source adapter
          |
   validate + normalize
          |
   ingestion_run
      /       \
raw_snapshot  aggregate_snapshot
                    |
          compare with previous
          successful snapshot
                    |
             check_result
                    |
          alert / alert_event
                    |
                 Web UI
```

### Слои snapshots

`raw_snapshot` хранит строки rolling window вместе с `snapshot_date`,
`data_date`, стабильным `row_fingerprint`, идентификатором запуска и исходными
полями. На первом этапе допустимо хранить payload строки в `JSONB`, а наиболее
важные dimensions и metrics — отдельными типизированными колонками.

`aggregate_snapshot` хранит агрегаты на нескольких фиксированных grain:

1. `data_date`;
2. `data_date + channel`;
3. `data_date + channel + campaign`;
4. `data_date + channel + campaign + geo`;
5. `data_date + conversion_action`.

Для каждого grain сохраняются `row_count`, `impressions`, `clicks`, `cost`,
`completions`, `conversions`, `revenue` и, при необходимости, action-specific
conversion metrics.

Не следует группировать сразу по всем dimensions: это почти повторит raw слой,
сделает проверки дорогими и создаст шум. Более глубокий drill-down выполняется
по raw snapshot только для найденной аномалии.

## Жизненный цикл алерта

Алерт — это не одноразовая строка за день, а состояние инцидента.

- `OPEN`: проблема обнаружена впервые.
- `ONGOING`: проблема подтверждается следующим запуском.
- `RECOVERED`: данные снова в норме, ожидается подтверждение.
- `RESOLVED`: восстановление подтверждено (по умолчанию двумя успешными
  снимками подряд).
- `ACKNOWLEDGED`: человек увидел инцидент; это не означает исправление.

Один логический инцидент определяется стабильным `alert_key`, построенным из
`check_type + grain + dimension values + affected data_date/range`. Каждый
дневной результат добавляется в `alert_event`, поэтому UI показывает и текущее
состояние, и всю историю. Если значение по-прежнему сломано, алерт остаётся
`ONGOING`, даже если новый спад относительно вчерашнего дня отсутствует.

Сравнение выполняется не только `today vs yesterday`: дополнительно хранится
baseline последнего известного корректного значения. Это не даёт поломке
«стать новой нормой» на следующий день.

## Первые проверки

- Полностью отсутствующая дата.
- Снижение `row_count` по date/channel/campaign/geo.
- Исчезновение dimension member (channel, campaign, geo).
- Исчезновение conversion action.
- Снижение conversions/revenue за закрытые даты.
- Spend/clicks/impressions присутствуют, conversions/revenue исчезли.
- Резкое изменение ключевых metrics относительно прошлого snapshot.
- Schema drift: пропавшие, новые или сменившие тип колонки.
- Stale source: максимальная `data_date` не обновилась вовремя.

Порог проверки должен поддерживать одновременно абсолютное и относительное
условие, например: падение больше 20% **и** больше 10 conversions. Это снижает
шум на малых объёмах. Порог и допустимый лаг последних дат задаются в
конфигурации.

## Безопасность и эксплуатация

- Ingest-токены хранятся в БД только в виде SHA-256 hash.
- Snapshot публикуется только после успешного завершения всей загрузки.
- Сравниваются последние два `SUCCESS` запуска, а не календарные даты.
- Повторный запуск за одну `snapshot_date` должен быть идемпотентным.
- В БД хранятся `source_updated_at`, время запуска, статус, число строк и
  checksum.
- Для raw snapshots задаётся retention; агрегаты и alert history хранятся
  дольше.

## Подключение приватной Google Sheet

Для MVP используется push-модель без Google Cloud и service account:

1. Создать проект на вкладке «Источники» и сохранить показанный ingest token.
2. Скопировать [Apps Script](apps_script/Code.gs) в Extensions → Apps Script
   внутри нужной Google Sheet.
3. Подставить `API_URL`, `INGEST_TOKEN` и название worksheet.
4. Один раз запустить `testHookyConnection` и разрешить скрипту доступ к текущей
   таблице и отправку HTTP-запроса.
5. Запустить `installDailyTrigger`.

Таблица остаётся приватной. Apps Script выполняется от имени её владельца и
отправляет snapshot на Hooky Checker по HTTPS. Сервер определяет проект по
токену, но хранит только hash токена.

## Что потребуется от владельца данных

- Имя проекта и worksheet.
- Точный mapping колонок, особенно conversion actions.
- Часовой пояс и время ежедневного запуска.
- Начало/конец flight либо правило rolling window.
- Какие даты считаются закрытыми и после какого лага.
- Начальные thresholds и список критичных dimensions/metrics.

Подробный план находится в [TODO.md](TODO.md), правила дальнейшей разработки —
в [AGENTS.md](AGENTS.md).

## Локальный запуск

```powershell
python -m pip install -e ".[dev]"
hooky-checker init-db
hooky-checker serve
```

Команда поднимает UI и ingestion API вместе на `http://localhost:8000`.

По умолчанию используется локальная SQLite база `hooky_checker.db`. Для
PostgreSQL достаточно задать `DATABASE_URL`.
