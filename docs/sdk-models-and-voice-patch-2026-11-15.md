# Прямой SDK: модели, STT, Voice/TTS и Tab

Дата проверки: 2026-11-15. Native **patch version 4**, Windows x64. Изменения включены в MirrorPatch 1.5.0; в релизе 1.4.0 их не было.

## Результат и границы

Исправление применено к установленным Manager 2.32.1, IDE Hub 1.5.9 и CLI 1.5.9. Сквозные проверки выполнялись через Python SDK 0.2.0 из `F:/projects/antigravity_engine_sdk` и backend Manager. У аккаунта, по сообщению пользователя, нет платной подписки.

По указанию пользователя скрытые модели **не добавляются в UI**. Рендерер, frontend, списки моделей интерфейса и пользовательские настройки не изменены. Доступ предоставляется прямым SDK/RPC-вызовам; лицензии, параметры entitlement, содержимое запросов и квоты не подменяются.

## Изменения

**Полный published-каталог.** `candidates()` сохраняет серверный порядок agent/tiered-моделей для выбора по умолчанию, затем добавляет остальные `availableExperiences` с непустым `model.name` или `modelId`. Поэтому Flash Lite и image-модель больше не отсекаются локально. Записи без реальной модели исключаются.

**Точный выбор.** Неизвестный RPC model ID теперь отклоняется, даже если CLI-флаг `--model` отсутствует. Автоматический default остаётся только для пустого выбора и аудированного устаревшего native default `chat-gemini-3.1-pro-low`, когда явного CLI-выбора нет. Непубликуемый `tab_*` получает отдельное сообщение о неподдерживаемом AICode-маршруте.

**STT gRPC.** `AICodeGRPCEndpoint` раньше вычислял TLS target из `AICODE_ENDPOINT_URL`, направленного в plaintext HTTP-адаптер. Теперь он возвращает существующую read-only строку `aicode.googleapis.com:443`, сохраняя исходные prologue, argument spills и epilogue. Новых вызовов или кадров не добавлено.

**Voice/TTS RAP.** Голосовой режим использует `/voice/ws`, подпротокол `jetski-voice-proto` и gRPC `/google.gca.aicode.v1alpha.JetskiRemoteAgentProxyService/StreamVoice`. Его target отдельно строится в `getAICodeRemoteURL`: исправление STT на него не влияло. WebSocket открывался, но закрывался кодом 1011 с `tls: first record does not look like a TLS handshake`, что соответствовало `Couldn't connect` в GUI.

Чтение HTTP-переопределения в `getAICodeRemoteURL` заменено пустой Go-string. Исходный код выбирает свой production URL. Нативные OAuth, авторизация, protobuf и обработка аудио сохранены. HTTP predictions/config/quotas остаются в локальном адаптере. Скрытые модели и значения лицензии локально не создаются.

## Проверка установленного backend через SDK

Рабочие каталоги временные и пустые; MCP отключён. Использованы синтетические фразы и PCM, без микрофона и файлов пользователя. Для Voice создан собственный пустой SDK cascade. OAuth-коды, токены и заголовки не экспортировались.

| Проверка | Реальный результат |
| --- | --- |
| `complete(model="gemini-3.5-flash-lite")` | `PATCH_V3_OK`, в том числе после прогрева |
| Gemini 3.8 Flash High, Claude Sonnet 4.6 | Контрольные ответы `PATCH_V3_OK` |
| `generate_image()` | `generateImage.modelName=gemini-3.1-flash-image`, непустой JPEG и native `finish`; planner Gemini 3.8 Flash Low |
| `live_transcription(model="models/proactive-observer-v10")` | Точная фраза `The blue robot counts seven yellow stars.`, `is_final=true`, `complete=true` |
| Voice/TTS на установленном patch v4 | HTTP 101, STARTING → LISTENING, **196 320 байт аудио**, фраза `Hello! I can confirm that your voice connection is working perfectly.` |
| Квоты | Группы Gemini и Claude/GPT поступают через штатный API; численные лимиты не вычислялись |
| Неизвестный `gemini-2.5-pro` | Подтверждённый отказ `absent from fetchConfig`, без подстановки другой модели |
| Перезапуск | Авторизация сохранена, `RESTART_OK` получен; в части прогонов после одного повтора того же запроса |

TTS проверялся через прямой Voice WebSocket: отправлен синтетический `VoiceClientMessage.mic_audio`, получены `VoiceServerMessage.voice_audio` и `voice_transcript`. Временная исправленная сборка также вернула 190 562 байта аудио. Имя внутренних TTS-весов RAP поток не сообщает: подтверждены сервис и звук, а не предположение об имени модели. `models/proactive-observer-v10` относится к отдельному STT.

Установленный v4 дополнительно вернул `PATCH_V4_OK` на Flash Lite с `max_invocations=3` за 1.49 с. Полученный голос упакован в `voice-installed-v4.wav`: mono PCM16LE / 24 кГц / 4.09 с. Частота согласована с серверным `audio_timing` (196 320 байт и 4090 мс); обратный `Client.transcribe(..., model="gemini-3.5-flash-lite")` точно восстановил приведённую фразу. Это проверка фактического звука, а не только текстового transcript.

### Дополнительная проверка нескольких реплик

После сообщения пользователя о `Model unavailable` выполнен более длительный тест одной Voice-сессии с **Gemini 3.8 Flash Low** (`plan_model=1320`). Получены три завершённых ответа: приветствие, результат **17 × 23 = 391** с объяснением, затем **391 × 2 = 782**. Сохранился контекст; совпадают spoken transcript и planner response. Основные запросы шли с `requested=selected=gemini-3.8-flash-low`, HTTP 200; в траектории ошибок нет. Проверка ждала результат основного planner, а не заканчивалась после голосового «сейчас посчитаю». Доказательство: `voice-multi-low-audit.json`.

Пользователь также подтвердил нормальный диалог из нескольких сообщений в другом разговоре. Исходный `Model unavailable` не воспроизведён; постоянный дефект по этому симптому не установлен. Новых изменений production-кода при этой диагностике не было.

Отдельно в длительном тесте зафиксирован **ResourceExhausted** локального live transcriber: кратковременная ёмкость STT исчерпана, сервер указывал восстановление примерно через 1–3 минуты. При этом голосовой диалог завершился успешно. Это отдельный лимит, который не следует отождествлять с недельными группами quota summary или доказанной причиной ошибки пользовательского диалога.

## Tab: откуда берётся 404

Дополнено 2026-11-16: [сравнение пути официального IDE и native-конвертеров](tab-ide-route-2026-11-16.md).
Без requestedModelId отказ сохраняется; исходный конвертер отправляет другое,
тоже недоступное experience. Это отдельно проверено на свежих сетевых вызовах.

Диагностические relay сохраняли нативную авторизацию и направляли запросы только на фиксированные штатные HTTPS-домены. Записывались метод, путь, model/experience и статус, без содержимого запросов или OAuth-заголовков.

| Фактический вызов | Ответ |
| --- | --- |
| `POST https://aicode.googleapis.com/v1alpha:streamGenerateContent`, experience `tab_flash_lite_preview` | **404 NOT_FOUND**, `Requested entity was not found.` |
| `POST https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent` | **403 PERMISSION_DENIED**, лицензия, `#3501` |

На том же AICode-канале `fetchLicenses` и `fetchConfig` отвечают 200. Отклоняется prediction с непубликуемым Tab experience. Оба ID — `tab_flash_lite_preview` и `tab_jump_flash_lite_preview` — дополнительно проверены через специализированный `HandleStreamingCommand`: прямой AICode дал 404, старый CCPA — 403 `#3501`.

Для этих диагностик 2.32.1 временно добавлялась в Python tuple проверенных editor-версий; исходники SDK не менялись. Bootstrap вернул 0 data frames и terminal `{}`. Штатный SDK пока разрешает независимые editor-команды только для 2.24.0/2.25.0. Промежуточные filter/diff frames при ошибочном terminal не считались успешной генерацией.

**Рабочий бесплатный маршрут исходных Tab-моделей не найден.** Подмена их обычной Flash Lite не выполняется и не была бы доказательством Tab-доступа. Причины серверного отсутствия Tab experience в AICode неизвестны; его доступность в другом тарифе или маршруте этим не устанавливается.

## Установка и native окна

| Установка | Путь относительно профиля |
| --- | --- |
| Manager | `%LOCALAPPDATA%/Programs/antigravity/resources/bin/language_server.exe` |
| IDE Hub | `.gemini/bin/agy.exe` |
| CLI | `%LOCALAPPDATA%/agy/bin/agy.exe` |

Установщик обновляет intact patch v1/v2/v3/v4, проверяет файлы, сохраняет первый `*.before-aicode` и откатывает замену при ошибке. Неизвестные и изменённые сборки не принимаются. Оставшиеся после подтверждённого закрытия GUI фоновые backend останавливались для замены файла.

Новые окна относительно patch v2:

| Профиль | Offset | До | После |
| --- | --- | --- | --- |
| CLI/Hub STT | `0x2742c9f` | `90e8fba90300e856cbbdfd4885db751b4889442450` | `488d058c91b601bb19000000e91e00000090909090` |
| Manager STT | `0x2809c3f` | `90e8fb790300e836b5affd4885db751b4889442450` | `488d057b5d3201bb19000000e91e00000090909090` |
| CLI/Hub RAP | `0x2788b2e` | `e84d789afd` | `31c031db90` |
| Manager RAP | `0x284c74e` | `e8cd218efd` | `31c031db90` |

SHA-256 native patch v4:

```text
CLI/Hub:          8c033def5d690d24bd8d2552517a7086965858e374d88091071466e263d09cec
Manager gate:     b3fec35b68d5599ae0c0421304dee1e42de7cb1615bbe152a85e6383abecd9da
Manager pristine: 62bd5e6ebdc97b10bb3f2359987d770a45158f41f87f4db01e449ec56d25df9d
```

## Ограничения и доказательства

- **84 Python tests**, `go test ./...` и `go vet ./...` прошли. PE-тест отдельно проверяет read-only host и исходный epilogue; fixture-тесты допускают изменения только в перечисленных pinned окнах. Проверяются rollback и обновления v2/v3, выбор/неоднозначность моделей, сохранение entitlement и изоляция OAuth-кэша.
- Полная живая SDK-проверка относится к Manager. Hub/CLI обновлены и проверены по manifest; CLI дал `CLI_V3_OK` на Gemini 3.8 Flash Low. Сквозные аудиосценарии их профилей не квалифицированы.
- CLI `--model gemini-3.5-flash-lite` пока отклоняется встроенным селектором до запроса: он использует старый `fetchAvailableModels`. Требуемый доступ обеспечивается прямым SDK/RPC, без расширения UI.
- При холодном старте SDK сохраняются таймауты первого запроса. После него тот же ID отвечает, авторизация действительна. Эта задержка наблюдалась до изменения и не объявляется устранённой.
- Проверены personal-аккаунт, production endpoints и перечисленные Windows-сборки. Enterprise/ADC/API-key, тестовые endpoints и другие версии не квалифицированы. GUI-голос с физическим микрофоном не проверялся.

Локальные материалы исключены из Git: `test_cache/specialized-patch-2026-11-15/`. Основные доказательства: `installed-v4-final.json`, `manager-applied-v4.json`, `voice-installed-v4.json` и его аудиофайл, `tab-network-routes.json`. Полный model/image/restart прогон до дополнительного RAP-окна сохранён в `manager-applied-v3.json`; код этих путей в v4 сохранён.

Сценарии: `verify_sdk.py`, `probe_voice.py`, `voice_proto.py`, `extract_voice_schema.py`, `trace_tab_routes.py`. Они выполняют реальные запросы и расходуют квоты. Дополнительные первоначальные проверки находятся в `F:/projects/antigravity_engine_sdk/reports/catalog-2026-11-15/`. Исходники SDK и его предсуществующие web-изменения сохранены.
