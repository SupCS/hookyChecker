# MVP TODO

## Phase 0 — согласовать контракт данных

- [ ] Получить Google Sheet ID/URL и worksheet.
- [ ] Снять фактическую схему, типы, примеры строк и объём данных.
- [ ] Зафиксировать timezone, flight window и расписание загрузки.
- [ ] Согласовать canonical names и mapping исходных колонок.
- [ ] Определить, являются ли conversion actions отдельными metric columns или
      строками dimension.
- [ ] Согласовать закрытые даты, grace period и начальные thresholds.
- [ ] Определить уникальность строки либо стратегию `row_fingerprint`.

## Phase 1 — фундамент приложения

- [x] Создать Python project (`src/`, `tests/`, `pyproject.toml`).
- [x] Добавить settings и `.env.example` без секретов.
- [ ] Поднять PostgreSQL локально и на Railway.
- [ ] Настроить SQLAlchemy и Alembic.
- [ ] Добавить structured logging.
- [x] Добавить таблицу `ingestion_run`.
- [ ] Добавить CI: lint, type check, tests.

## Phase 2 — ingestion и snapshots

- [x] Описать интерфейс `SourceAdapter`.
- [x] Реализовать публичный CSV-вариант `GoogleSheetsAdapter`.
- [x] Реализовать приватную доставку snapshots через Apps Script webhook.
- [x] Добавить базовый реестр проектов/источников и ingest-токены.
- [ ] Реализовать нормализацию дат, чисел, null и названий колонок.
- [ ] Валидировать обязательные колонки и фиксировать schema drift.
- [x] Создать базовые модели `raw_snapshot` и `aggregate_snapshot`.
- [x] Реализовать транзакционную публикацию raw snapshot.
- [x] Добавить идемпотентный повторный запуск одинакового snapshot.
- [x] Добавить checksum и source row count.
- [ ] Добавить aggregate snapshot и метрики выполнения.

## Phase 3 — checks engine

- [x] Описать единый результат проверки: severity, check type, grain,
      dimensions, expected, actual, delta, threshold и evidence.
- [x] Реализовать чистую проверку missing dates.
- [x] Реализовать основу проверки metric/row count drops.
- [ ] Реализовать missing channel/campaign/geo.
- [ ] Реализовать missing conversion action.
- [ ] Реализовать regression closed-date conversions/revenue.
- [ ] Реализовать spend-without-outcome.
- [ ] Реализовать freshness/staleness.
- [ ] Реализовать configurable absolute + relative thresholds.
- [ ] Сохранять baseline последнего корректного значения.
- [ ] Покрыть каждую проверку synthetic fixtures и boundary tests.

## Phase 4 — alerts lifecycle

- [x] Создать базовые модели `alert` и `alert_event`.
- [ ] Генерировать стабильный `alert_key`.
- [ ] Реализовать `OPEN -> ONGOING -> RECOVERED -> RESOLVED`.
- [ ] Требовать два успешных подтверждения перед auto-resolve.
- [ ] Добавить acknowledge, comment и assignee.
- [ ] Не создавать дубль инцидента при повторном запуске.
- [ ] Сохранять evidence и ссылку на сравниваемые snapshots.

## Phase 5 — Web UI

- [x] Dashboard выбранного `SUCCESS` snapshot с live-фильтрами campaign/channel/location/date
      и performance-виджетами по channel/location/month.
- [x] Добавить мультивыбор месяцев и сравнение периодов в performance dashboard.

- [x] Создать базовый Overview: последний запуск, число open/critical alerts.
- [ ] Явный блок сегодняшних новых и продолжающихся алертов.
- [ ] История с фильтрами по статусу, severity, check, дате и dimensions.
- [ ] Карточка инцидента с timeline событий.
- [ ] Side-by-side expected/actual/delta.
- [ ] Drill-down до изменившихся raw rows.
- [ ] Графики metric history по выбранному grain.
- [ ] Кнопки acknowledge/comment.
- [ ] CSV export отфильтрованных результатов.

## Phase 6 — deploy и эксплуатация

- [ ] Добавить Dockerfile и Railway config.
- [ ] Развернуть web service, PostgreSQL и daily cron service.
- [ ] Настроить health/readiness checks.
- [ ] Добавить advisory lock против параллельных ingestion runs.
- [ ] Настроить retention raw snapshots и очистку.
- [ ] Настроить резервное копирование PostgreSQL.
- [ ] Добавить уведомления о падении самого pipeline.
- [ ] Провести shadow run минимум 7 дней и откалибровать thresholds.

## После MVP

- [ ] Slack/email notifications.
- [ ] UI для настройки checks и thresholds.
- [ ] Сезонные baselines и robust statistics.
- [ ] Сверка reporting source с dashboard extract/source.
- [ ] Multi-source и multi-client support.
- [ ] RBAC/SSO и audit log.
- [ ] Перенос больших raw snapshots в BigQuery при росте объёма.

## Definition of Done MVP

- Ежедневный snapshot запускается автоматически и идемпотентно.
- Raw и aggregate snapshots доступны для расследования.
- Все проверки Phase 3 имеют тесты и сохраняют evidence.
- Поломка не становится нормой после одного дня: alert остаётся открытым.
- UI показывает новые, ongoing и resolved incidents и позволяет drill-down.
- Ошибка загрузки не создаёт ложный snapshot и видна в UI.
- Секреты не находятся в репозитории или логах.
