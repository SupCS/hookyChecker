# Инструкции для дальнейшей разработки

## Цель

Разрабатывать Hooky Checker как простой, объяснимый и надёжный MVP контроля
качества данных. Приоритет — быстро обнаружить поломку, локализовать её и
сохранить доказательства изменения; автоматическое исправление данных пока не
входит в scope.

## Обязательные архитектурные правила

1. Разделять `snapshot_date` (время наблюдения) и `data_date` (дата данных).
2. Никогда не считать неуспешную/частичную загрузку валидным snapshot.
3. Сравнивать только опубликованные `SUCCESS` snapshots.
4. Сохранять raw и aggregate слои; aggregate не заменяет raw.
5. Source-specific код держать за интерфейсом `SourceAdapter`.
6. Checks должны быть чистыми и детерминированными: вход — два snapshot/baseline
   и config, выход — структурированные результаты.
7. Alert lifecycle отделять от check result. Повторное обнаружение обновляет
   существующий incident через `alert_key`, а не создаёт новый.
8. Не auto-resolve инцидент после одного чистого запуска; по умолчанию нужны два.
9. Использовать UTC в технических timestamp, бизнес timezone хранить явно.
10. Все thresholds и grace periods задавать конфигурацией, не прятать в UI/SQL.

## Предпочтительная структура проекта

```text
src/hooky_checker/
  adapters/        # optional public Google Sheets reader
  checks/          # независимые data quality checks
  db/              # SQLAlchemy models, repositories
  pipeline/        # ingestion, snapshot, comparison orchestration
  alerts/          # incident lifecycle
  templates/       # server-rendered web UI
  config.py
tests/
  unit/
  integration/
migrations/
```

## Рабочие соглашения

- Перед реализацией сверяться с `README.md` и актуализировать `TODO.md`.
- Не коммитить credentials, sheet contents или клиентские данные.
- Денежные значения хранить как `NUMERIC/Decimal`, не `float`.
- Исходные даты парсить строго; неоднозначные значения отправлять в failed run.
- Нормализовать null/пустые строки предсказуемо, сохраняя raw payload.
- Fingerprint строить из canonical representation и version алгоритма.
- Запросы UI не должны сканировать весь raw слой без фильтра.
- Для каждого нового check добавлять:
  - synthetic fixture;
  - happy path;
  - anomaly case;
  - threshold boundary;
  - null/missing dimension case;
  - стабильный `alert_key`.
- Миграции должны быть обратимо или явно безопасно применимы к существующим
  snapshot/alert данным.
- После изменений запускать formatter, lint и релевантные тесты.

## Решения, которые нельзя угадывать

До получения реального источника не фиксировать без подтверждения:

- точные типы и названия всех колонок;
- row business key;
- семантику каждой conversion-action колонки;
- thresholds и severity;
- допустимый source lag;
- raw retention и ожидаемый объём.

В этих местах сначала профилировать источник, затем документировать принятое
решение и только после этого писать необратимую схему/логику.

## Очерёдность ближайшей реализации

1. Получить первый snapshot через Apps Script и профилировать его схему.
2. Создать data contract и профилирование.
3. Реализовать БД, ingestion run и атомарные snapshots.
4. Реализовать checks и baseline.
5. Реализовать lifecycle incidents.
6. Построить UI и только затем добавить deployment automation.
