# Рабочий бесплатный ответ agy через AICode

Дата: 2026-11-15.

## Цель и достигнутый результат

Найти штатную ветку получения бесплатного доступа, отличную от отклоняемого `onboardUser`, проверить необходимость другого `ideType` и получить реальный ответ модели на текущем аккаунте без подписки.

Получены два реальных ответа от Gemini 3.8 Flash (Low):

| Проверка | Результат | Входные токены | Выходные токены |
| --- | --- | --- | --- |
| Нативный CLI с аппаратным наблюдением HTTP | HTTP 200, exit 0, `SERVICE_OK` | 10 762 | 3 |
| Новый процесс без отладчика через подготовленный launcher | exit 0, `AGY_FREE_OK` | 10 761 | 6 |

Серверный ответ `GET /v1alpha:fetchLicenses` содержит `userTier=free-tier`, `tierDisplayName=Antigravity Starter Quota`. Идентичность успешного запуска совпала с прежним аккаунтом; персональные значения в отчёт не включены. Использовался сохранённый свежий OAuth, полученный самой 1.4.5 на предыдущем этапе. Новый браузерный вход, API key, Cloud-проект и платная лицензия не потребовались.

Рабочая тестовая связка: встроенная личная ветка AICode, включённая внутренней переменной `USE_AICODE=true`, и исправление устаревшего имени experience в отдельной копии официального 1.5.9. Experience здесь означает именованную серверную конфигурацию модели. Установленные бинарники и продуктовый код патчера не менялись.

## Существенное уточнение предыдущего вывода

Отказ `onboardUser / UNSUPPORTED_LOCATION` доказан для прежнего пути Cloud Code. Он не означает отсутствие бесплатного доступа через все встроенные API клиента. Ранее проверенный AICode давал HTTP 404; причина этого результата тогда не была локализована.

Теперь установлено: AICode действительно возвращает бесплатный тариф, а запрос генерации официального 1.5.9 использует жёстко заданное устаревшее имя experience. После замены только этого имени на значение, предложенное самим сервером, тот же клиент получает реальный ответ. Таким образом, успешный `onboardUser` не является необходимым условием найденного рабочего пути.

## Проверка официального клиента

Прочитан актуальный [официальный установщик](https://antigravity.google/cli/install.ps1), без его исполнения. Он указывает на [release manifest Windows x64](https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/windows_amd64.json). На дату проверки manifest возвращает версию 1.5.9.

SHA-512 имеющейся исходной копии полностью совпал с manifest:

```text
539a9415a686d9ec45f5c59fe7d32c485c6fa9abaea50208ae8ede4ed3622b9fb2bfd6680c87dc52344981172a96ddacb261ff4231276b5572fb5b62a9b0221e
```

SHA-256 исходной копии:

```text
8803c289cac04d6e53f2211592d9981c2d5760bc0d9b4a7f222ef6ecc7abb38a
```

Это тот же официальный бинарник, а не отдельная неподтверждённая сборка Hub с отличающимся путём регистрации.

## Какие пути активации найдены

| Кандидат | Что делает проверенный код | Итог |
| --- | --- | --- |
| Личный Cloud Code | `loadCodeAssist`, затем `onboardUser` при отсутствии текущего тарифа | Ранее воспроизведён 403 `UNSUPPORTED_LOCATION / 1008` |
| Личный AICode | `FetchLicensesFromAICode`, преобразование выданного `userTier`, затем inference AICode | Рабочий путь этого этапа |
| `SelfAssignLicense` | Gemini Enterprise, Cloud-проект и location | Не является регистрацией личного free-tier; вызов назначения не выполнялся |
| `RegisterGdmUser` | В `CodeAssistClient` читает cache LoadCodeAssist и возвращает пустой успешный объект | Самостоятельного запроса активации в этой реализации нет |
| `ResetOnboarding`, `SkipOnboarding`, `AcceptTermsOfService` | Deprecated RPC, возвращающие ошибку о прекращении поддержки | Не являются альтернативой регистрации |
| `maybeRegisterProductAPI` | Регистрирует обработчики локального HTTP mux | Не регистрирует аккаунт у поставщика |
| Gemini API key | Отдельный документированный provider `gemini` | Не использовался и не засчитывается как активация account free-tier |

Официальная [документация входа](https://antigravity.google/docs/install?tab=cli) различает личный OAuth, Enterprise и Gemini API key. Последний направляет запросы непосредственно в Gemini API и не создаёт account session, поэтому его нельзя выдавать за исправление рассматриваемого бесплатного доступа.

### `ideType`

В протоколе проверенного бинарника нет отдельного `ANTIGRAVITY_CLI` или `AGY_CLI`. Найдено 14 значений, включая `IDE_UNSPECIFIED=0`, `ANTIGRAVITY=9`, `JETSKI=10` и значения отдельных IDE. В `fetchLoadCodeAssistResponse` и `OnboardUser` значение 9 записывается непосредственно кодом:

- `fetchLoadCodeAssistResponse`: VA `0x14279ba04`;
- `OnboardUser`: VA `0x14279c9a5`.

У официального [Gemini CLI](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/code_assist/setup.ts) действительно другая metadata: `IDE_UNSPECIFIED`, `PLATFORM_UNSPECIFIED`, `pluginType=GEMINI`; он также использует `onboardUser`. Это клиент другого продукта. Подмена его metadata в Antigravity для найденного рабочего решения не понадобилась.

## Действительный бесплатный тариф в AICode

Включение `USE_AICODE=true` меняет `useAICodePredictionFlow`. В `fetchLoadCodeAssistResponse` проверенная ветка вызывает собственную `FetchLicensesFromAICode`, а не прежний HTTP `loadCodeAssist`.

Наблюдаемый запрос и выбранные поля настоящего серверного ответа:

```text
GET https://aicode.googleapis.com/v1alpha:fetchLicenses
HTTP 200
```

```json
{
  "licenses": [
    {
      "userTier": "free-tier",
      "tierDisplayName": "Antigravity Starter Quota"
    }
  ]
}
```

Это не только локальная строка Pro или значение по умолчанию. Хотя `userTierFromLicenses` умеет создавать fallback `free-tier`, в данном тесте тело HTTP-ответа само содержит указанный тариф. Неизменённый клиент преобразует его в свои `currentTier` и `paidTier` с ID `free-tier`; название поля `paidTier` в совместимом локальном представлении не делает этот тариф платным.

Поэтому при AICode уже имеется текущий тариф, клиент не нуждается в прежнем `onboardUser`. Локальные проверки проходят и на исходном бинарнике без CLI/Manager gate.

## Почему AICode до исправления возвращал 404

`GET https://aicode.googleapis.com/v1alpha:fetchConfig` возвращает `availableExperiences` и `agySurfaceConfig`. Среди agent experiences сервер выдал:

```text
gemini-3.8-flash-high
gemini-3.8-flash-medium
gemini-3.8-flash-low
```

Но `aicodeConverter.ToRequestPayload` в официальном 1.5.9 не использует выбранное имя для этого поля. Код безусловно создаёт experience со строкой:

```text
chat-gemini-3.1-pro-low
```

Её нет в полученной серверной конфигурации. В исходном тесте с `USE_AICODE=true` клиент получает настоящий `free-tier`, проходит проверки, но его `POST /v1alpha:streamGenerateContent` возвращает HTTP 404 `NOT_FOUND`.

В отдельной тестовой копии имя заменено на выданное сервером `gemini-3.8-flash-low`; CLI также явно запущен с этой моделью. Результат того же inference endpoint стал HTTP 200, а native CLI вернул текст модели и ненулевую статистику токенов. Переход от 404 к 200 после этого ограниченного изменения подтверждён контрольными запусками.

## Ограниченное исправление

Копия основана на исходном официальном бинарнике. В ней нет предыдущих обходов eligibility, Manager gate или исправлений результатов авторизации. Изменены только длина строки и относительный адрес существующей строки модели в двух соседних инструкциях:

| VA | Исходные байты | Новые байты |
| --- | --- | --- |
| `0x142783fb6` | `48c7401017000000` | `48c7401014000000` |
| `0x142783fbe` | `488d15eb80b001` | `488d151003ad01` |

Полное сравнение файлов подтвердило четыре фактически изменённых байта и неизменный размер. Значения тарифа, OAuth, заголовки и серверные ответы не изменялись.

SHA-256 рабочей тестовой копии:

```text
cde0bbbaa5382f4c8b5f7690a5006fe87750ca7f9c9364ae1ca275cf32d43607
```

Имя `gemini-3.8-flash-low` проверено по реальному `fetchConfig` перед созданием копии. Копия предназначена для этого конкретного model experience. Она не является готовым универсальным исправлением динамического выбора всех моделей.

## Воспроизводимый запуск

Подготовлен и проверен без отладчика локальный launcher:

```powershell
pwsh -NoProfile -File "F:\projects\patch-agy\mirror-patch\test_cache\free-activation-audit-2026-11-15\run-agy-free.ps1"
```

Собственный короткий запрос:

```powershell
pwsh -NoProfile -File "F:\projects\patch-agy\mirror-patch\test_cache\free-activation-audit-2026-11-15\run-agy-free.ps1" -Prompt "Ответь одним коротким предложением: соединение работает? Не используй инструменты."
```

Launcher проверяет SHA-256 тестовой копии, включает AICode только для запуска, явно выбирает `gemini-3.8-flash-low`, использует изолированный профиль и пустой рабочий каталог. Он использует тот же локальный proxy `127.0.0.1:10809`, что и выполненные проверки. Параметры окружения и текущий каталог вызывающего PowerShell восстанавливаются в `finally`. Проверенный движок — PowerShell 7 (`pwsh`).

Тест без отладчика вернул:

```json
{
  "status": "SUCCESS",
  "response": "AGY_FREE_OK\n",
  "num_turns": 1,
  "usage": {
    "input_tokens": 10761,
    "output_tokens": 6,
    "thinking_tokens": 0,
    "cache_read_tokens": 0,
    "total_tokens": 10767
  }
}
```

## Границы результата

- Получен реальный ответ через нативный agy и server-advertised бесплатный тариф текущего аккаунта.
- Ветка `USE_AICODE` внутренняя; она не найдена как документированный пользовательский переключатель. Серверные API и набор experiences могут измениться.
- Проверена Gemini 3.8 Flash (Low) и простой текстовый ответ. Другие модели, полноценная работа инструментов и интерфейс Hub/IDE не квалифицированы.
- Счётчики показывают около 10,7 тысячи входных токенов даже для короткого запроса из-за штатного контекста агента; выполнены только два успешных контрольных запроса. Бесплатные лимиты остаются применимыми.
- Legacy Cloud Code и его `onboardUser` по-прежнему имеют отдельно подтверждённый отказ. Историческая причина работоспособности старого патча не восстановлена; найден текущий работающий путь.
- Установленная команда `agy` не заменялась и автоматически на этот launcher не переключалась.

## Артефакты

`test_cache/free-activation-audit-2026-11-15/`:

- `official-install.ps1`, `official-current-manifest.json`;
- `ide-type-enum.json`, `candidate-functions.json`;
- `prepare_aicode_experience_test.py`, `aicode-experience-test-manifest.json`;
- `agy.aicode-experience-test.exe`, `run-agy-free.ps1`;
- `free-launcher-verification.json`, `working-free-path-proof.json`.

Трассы в `test_cache/auth-version-comparison-2026-11-15/`:

- `network-1.5.9-enroll-daily-aicode-detail.json`: исходный официальный клиент, реальные лицензия/config, генерация 404;
- `network-1.5.9-enroll-daily-aicode-detail-experience-test.json`: ограниченное исправление, реальные лицензия/config, генерация HTTP 200.

Во всех использованных окончательных аппаратных трассах `observation_errors=[]`. Тела OAuth и значения токенов не читались и не сохранялись. Серверные licensing/config JSON сохранялись только после редактирования иных полей. Коммитов и публикации не было.
