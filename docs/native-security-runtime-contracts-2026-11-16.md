# Runtime-контракты безопасности и происхождение отказов

Дата: 2026-11-16. Manager native 2.32.1, собственные изолированные SDK-сессии.
Продолжение [статического исследования](native-security-modules-2026-11-16.md).

## Установленный результат

Механизм command assessor разрешён серверной конфигурацией и инициализируется
в наших контрольных SDK-сессиях. Но **три найденных embedded-монитора не
допускаются текущей конфигурацией к созданию исполняемой цели**: все три имеют
`experimental_only=true`, а сервер передаёт `allow-experimental-monitors=false`.
В `createMonitoredTarget` подтверждена соответствующая проверка с возвратом
пустой цели. Это вывод из фактических флагов и реализации контракта; счётчик
инструкций каждого монитора отладчиком не измерялся.

Их запуск или блокирование в контрольных запросах не наблюдались. Ни серверной
модерации, ни обычного текстового отказа на этих контролях также не было.
По уточнению пользователя исследование выполнено без примера отказа и без
специально провоцирующих запросов. Исторические отказы не атрибутированы.

## Что прочитано с сервера

Временный наблюдатель читает метаданные **реального `fetchConfig` ответа**
AICode, не меняя запросы, ответы и настройки безопасности.

| Поле | Полученное значение |
| --- | --- |
| `enable-command-assessor` | `true` |
| `disable-command-assessor` | `false` |
| `allow-experimental-monitors` | `false` |
| `allow-pss-experimental-monitors` | `false` |
| `sonar-monitors-config` | Пустая строка, 0 символов |
| `policy-guardian-config` | Пустая строка, 0 символов |

Значения подтверждены и при генерации, и в отдельном metadata-only запуске.
Другие попавшие в allowlist флаги: `enable-swarm-monitor=true` и
`enable-conversation-memory-monitor=false`. Это другие компоненты; они не
считались доказательством запуска трёх SIR/tool-safety мониторов.

Локальные логи дополнительно подтвердили:

```text
externalMonitorConfigManager: initialized with embedded config (3 monitors)
[Sonar] Resolved active tags: [jetski_mode_default policy_guardian sonar_skip_tags]
```

Таким образом, загрузка трёх записей и вычисление тегов происходят реально.
Тег `policy_guardian` не является событием вызова `Guardian.Verify`.
Пустая строка override означает отсутствие этого override, а не отключение
всех альтернативных native hooks или серверных проверок.

## Матрица активности

| Компонент | Что подтверждено | Вывод для проверенных SDK-сессий |
| --- | --- | --- |
| Command assessor framework | Серверный enable=true/disable=false; runtime initialization и active tags | Разрешён и инициализируется |
| `agy_tool_safety` | Embedded enabled=true, experimental_only=true; допуск experimental=false | Не проходит контракт создания target |
| `sir_flash_lite` | То же; зависит от agy_tool_safety | Не проходит контракт создания target |
| `severe_internal_risks_agy` | То же; зависит от sir_flash_lite; embedded mode=BLOCK | BLOCK не становится активным только из-за записи в embedded config |
| `PolicyGuardianMonitor` | Регистрация и адаптер существуют; его записи нет в embedded defaults; override пуст | Выполнение Evaluate/Verify не подтверждено |
| Другие native permissions/hooks/sandbox | Существуют отдельные контракты | Их отключение из этих наблюдений не следует |
| Upstream content moderation | Формат ответа предусматривает собственные block/finish reasons | На контрольных вызовах блокировки не наблюдались |

Для exact field mapping experimental-признаков сопоставлены protobuf getter и
читаемое поле в `createMonitoredTarget`. Бинарник не патчился, ветви не
переключались, экспериментальные допуски не включались.

## Контракт происхождения отказа

Следует различать место принятия решения, а не только место вычислений:
локальный монитор сам может использовать серверную LLM и затем принять
локальное решение BLOCK по её нормальному ответу.

| Категория | Необходимое свидетельство | Чего недостаточно |
| --- | --- | --- |
| Локальный safety intervention | Событие монитора/Resolver с именем и решением, преобразованное в native hook result; либо доказанная цепочка native intervention в том же шаге | Название safety-пакета в бинарнике, активный тег, запись BLOCK в defaults |
| Upstream hard filtering | Исходный ответ upstream для того же запроса: `promptFeedback.blockReason` либо фильтрующий `candidates[].finishReason`, например SAFETY/PROHIBITED_CONTENT/BLOCKLIST/SPII | Произвольный HTTP 403, SDK `content_rejected`, слова о безопасности в задании |
| Текстовый отказ upstream | Текст отказа пришёл в исходном model response, при обычном STOP и без hard-filter marker | Нельзя по одному такому ответу разделить поведение весов, системную инструкцию и скрытые правила upstream |
| SDK-классификация | SDK разбирает уже полученный error/planner text и назначает `content_rejected` | Этот код не содержит надёжной атрибуции local/server |
| Авторизация/лицензия/лимит/маршрут | Соответствующий server error/status, request correlation и native details | Это не content moderation только по факту отказа |

Для Gemini смысл `promptFeedback`, `blockReason`, `finishReason` и safety ratings
сверен с [официальным контрактом GenerateContentResponse](https://aistudio.google.com/api-reference/generate-content).
Одно присутствие safety ratings тоже не равно блокировке; важны решение и
соответствующее значение enum. AICode использует собственный маршрут, поэтому
решающим является реально полученный сырой ответ, а не предположение по документации.

### Локальный hook

В `preToolInterventionToResult` найден контракт локального сообщения о
блокировке инструмента и сообщение, помечающее вмешательство safety monitor.
Есть отдельные преобразователи post-tool и post-invocation interventions.
Следовательно, обычный HTTP 200 от planner не исключает последующее локальное
блокирование инструмента.

### Отдельная телеметрия

Embedded `MonitorSignal` содержит:

```text
source, conversation_id, trajectory_id, step_index,
tool_call_name, triggered, reason, severity,
provenance, execution_id, outcome, duration_ms
```

`TelemetrySignalWriter.WriteSignal` преобразует сигнал в telemetry data;
это отдельный канал. RPC чтения `MonitorSignal` в исследованном
LanguageServerService не найден. Само отсутствие имени монитора в
`GetCascadeTrajectory` не доказывает, что он не выполнялся.

SDK запускает native с `--disable_telemetry`; этот исходный режим сохранён.
Отсутствие экспортированной телеметрии поэтому не использовалось как
самостоятельное доказательство неактивности.

`CortexStepErrorMessage.error` содержит `CortexErrorDetails`: user/model error
messages, structured parts, error code/ID и RPC details. В этом контракте нет
обязательного поля, однозначно называющего источник модерации. Для атрибуции
нужно коррелировать исходный upstream response, native intervention и шаг.

## Неоднозначность SDK `content_rejected`

Без изменения Python SDK проверены offline fixtures его существующего классификатора:

| Fixture, не live-событие | Результат SDK |
| --- | --- |
| Native error с текстом о локальном safety block | `content_rejected` |
| Native error с provider finish_reason SAFETY | `content_rejected` |
| License unavailable #3501 | `provider_error` в trajectory parser |
| Отказ prose вместо обязательного finish | `content_rejected` |
| Та же prose в контракте `complete()` | Буквальный текст возвращён вызывающему коду |

Это поведение классификации уже полученного ответа. Fixtures не выдаются за
наблюдавшиеся блокировки модели. Корректный finish-результат не отклоняется
только потому, что его текст обсуждает политику.

## Реальные контрольные вызовы

Для теста использован обычный маркер `Reply exactly: SAFETY_AUDIT_OK`, без
вредоносных запросов, jailbreak или переключения safety flags.

| Контракт | Модель | Результат |
| --- | --- | --- |
| `complete()` | Gemini 3.8 Flash Low | `SAFETY_AUDIT_OK` |
| `chat()` с настоящим finish | Gemini 3.8 Flash Low | `SAFETY_AUDIT_OK`, native FINISH step |
| `complete()` | Gemini 3.5 Flash Lite | `SAFETY_AUDIT_OK` |

Соответствующие upstream generation responses: HTTP 200, finishReason STOP,
без hard-filter block reasons и без native error/intervention в сохранённых
траекториях. Наблюдатель разбирал все полученные JSON/SSE frames этих ответов:
`invalid_json=0`, `oversized=false`.

На каждый диалог дополнительно приходил Flash Lite вызов с title-prompt marker.
В первом диагностическом прогоне его ответ действительно был названием
диалога. В дальнейшем сохранялся только признак типа запроса. Эти обращения
не считались safety inference: наличие второй модели не доказывает запуск SIR.
Признаков встроенных SIR prompts у наблюдавшихся запросов не обнаружено.

Дополнительная попытка запросить `list_dir` в собственном временном workspace
завершила RPC-цикл, но **не дала LIST_DIR step**. Она не засчитывается как
проверка исполнения реального файлового инструмента или срабатывания pre-tool
монитора. Доказательство допуска/исключения трёх мониторов основано на
конфигурации и контракте target, а не на этой попытке.

## Уточнение metadata RPC

Удалось уточнить причины двух прежних `provider_error`:

- `GetUserSettings`: HTTP 501, `unimplemented`.
- `GetPermissionSettings`: HTTP 400, `failed_precondition`, cloud conversations
  не включены для пользователя в этом процессе/контракте.

Это не доказательства ни цензуры, ни отсутствия платной лицензии. Поэтому
эффективные monitor-флаги читались из настоящего AICode `fetchConfig`, а не
делались выводы из ошибки settings RPC.

## Воспроизводимость и ограничения

Локальные ignored artifacts: `test_cache/safety-runtime-contracts-2026-11-16/`.

- `prepare_audit.py`, `adapter/` — временная наблюдающая сборка существующего
  adapter. Она сохраняет baseline-маршрутизацию, native hash validation,
  upstream authorization и response bytes. Устанавливаемые файлы не заменялись.
- `probe_controls.py`, `controls.json` — контрольные генерации, metadata,
  отобранные native logs и коррелированные network events.
- `metadata-only.json` — отдельная проверка server flags и пустых overrides.
- `monitor-contracts.json`, `provenance-schema.json` — контракты бинарника.
- `classification-fixtures.json` — явно помеченные offline SDK examples.
- `controls-initial.json` — отфильтрованный первоначальный контроль.

В отчёты не сохранялись OAuth/header values, пользовательские файлы, полные
system prompts, refusal prompts либо полные request/response bodies. В начале
аудита слишком широкий log matcher захватил синтетический текст и непрозрачную
thought signature; matcher сужен до нужных native source components, лишние
записи удалены из сохраняемых результатов.

Парсер наблюдателя проверен отдельным Go-тестом: chunked SSE с SAFETY marker
и JSON error, без экспорта prose. Production patch и SDK не менялись.
Собственные процессы завершены; временный audit-launcher executable удалён,
исходники наблюдателя и отобранные результаты сохранены для воспроизведения.
Native hash остался:

```text
b3fec35b68d5599ae0c0421304dee1e42de7cb1615bbe152a85e6383abecd9da
```

Выводы относятся к данным SDK-сессиям и полученной конфигурации, а не
автоматически ко всем IDE/CLI режимам, аккаунтам и будущим rollout.
Теги и загрузка framework — наблюдения; исключение experimental targets — вывод
по контракту. Запуск `Guardian.Verify`, реальный hard-filter отказ и причина
исторических отказов остаются неподтверждёнными.
