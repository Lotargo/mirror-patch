# AICode в установленных IDE и Manager, исправление загрузки квот

Дата: 2026-11-15. Статус: применено; пользователь подтвердил ответ модели в IDE
и отображение квот в обоих приложениях после перезапуска.

## Причина отказов и загрузки

CLI из PATH, backend IDE и backend Antigravity 2.0 являются разными установками.
Исправление обычного CLI не меняло два других backend. В IDE 2.7.0 окно входа
дополнительно самостоятельно выполняет старый `loadCodeAssist`, прежде чем
передать OAuth в Hub. Поэтому там сохранялся отказ по региону.

В Manager 2.32.1 генерация и квоты использовали CCPA. В журнале вызов
`retrieveUserQuotaSummary` получал HTTP 403, `SUBSCRIPTION_REQUIRED / 1001`, #3501.
После переноса генерации в AICode запрос квот всё ещё оставался на CCPA. В обеих
аудированных сборках `fetchRetrieveUserQuotaSummary` безусловно выполнял старый
запрос, хотя рядом уже присутствовали `FetchQuotaStatusFromAICode` и штатное
преобразование `quotaSummaryResponseFromQuotaStatus`. Ожидание не меняло маршрут.

## Что установлено

| Назначение | Файл относительно каталога пользователя | Профиль |
|---|---|---|
| IDE 2.7.0, вход | `%LOCALAPPDATA%/Programs/Antigravity IDE/resources/app/out/main.js` | `ide` |
| IDE Hub 1.5.9 | `.gemini/bin/agy.exe` | `hub`, patch version 2 |
| Antigravity 2.0 / Manager 2.32.1 | `%LOCALAPPDATA%/Programs/antigravity/resources/bin/language_server.exe` | `manager`, patch version 2 |
| Обычный CLI 1.5.9 | `%LOCALAPPDATA%/agy/bin/agy.exe` | `cli`, patch version 2 |

Вход IDE для personal-аккаунта запрашивает настоящий `GET
https://aicode.googleapis.com/v1alpha:fetchLicenses` через существующий OAuth
transport приложения. Серверный `userTier` переводится в совместимые поля
`currentTier`/`paidTier`; название поля `paidTier` не означает платную подписку.
Отсутствующая лицензия и сетевые отказы остаются ошибками, free-tier не создаётся
локально. Исходная ветка `isGcpTos` сохраняется, но enterprise не входит в живую
проверку этого исправления. Точный исходный hash frontend проверяется до записи.

В обоих native backend исправлены конвертеры experience и включён personal
AICode. Experience выбирается из действующего `fetchConfig`, как у исправленного
CLI. Штатный запрос квот теперь делегируется существующему
`FetchQuotaStatusFromAICode` через переход в начале функции до создания frame.
ABI receiver/context и результата совпадает; новые нативные вызовы и stack
frames не добавляются. Ответ и преобразование групп, процентов и дат остаются
штатными. Лимиты не увеличиваются и не подменяются.

Дополнительные окна patch v2:

| Сборка | File offset | До | После |
|---|---|---|---|
| CLI/Hub 1.5.9 | `0x279e7a0` | `4c8d6424d0` | `e95b84faff` |
| Manager 2.32.1 | `0x28623c0` | `4c8d6424d0` | `e9dbb7faff` |

Hash native-компонентов patch v2:

```
CLI/Hub: e903bb38c63a58a7e4bde7099ae02879f4ab65e6d7c1f5a772d2822e2aa26794
Manager (с прежним локальным gate): f6bbfcc78c30aff2d520605563888c5d026fe737c47046fb8597747f8ea5c83c
```

При закрытии IDE был наблюдён оставшийся native-процесс после завершения launcher.
Он завершён только после подтверждённого закрытия приложения. Launcher теперь
использует Windows Job Object с `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, чтобы
завершение оболочки также завершало native-компонент. [Описание структуры Job
Object у Microsoft](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information).

## Проверка

До установки новые профили проверялись на отдельных копиях. Hub вернул
`HUB_AICODE_OK`, Manager — `MANAGER_AICODE_OK`, в обоих случаях HTTP 200.
Установленный Manager также дал `MANAGER_AICODE_OK` через собственный backend.
Пользователь прислал скриншот с полноценным ответом Gemini Flash Low в IDE;
выбор Low был выбором интерфейса, не фиксированным значением патча.

Тестовые Hub и Manager с quota-переходом вернули HTTP 200 и группы `Gemini Models`
и `Claude and GPT models`, с weekly buckets, `remainingFraction` и `resetTime`.
Установленный Manager v2 повторил этот результат. На момент проверки сервер
сообщил `remainingFraction=1`; число взято из ответа сервера. После обновления
пользователь подтвердил: «Квоты видны в обоих». Повторный браузерный вход не
потребовался.

Один самостоятельный запуск Hub без IDE после обновления оказался `signed_out`
и не смог получить OAuth token source. Он не считается успешной проверкой квот:
Hub получает сессию от своего frontend, а отдельный процесс без этого соединения
не равнозначен открытому приложению. Вызов к живой IDE также не использовался как
доказательство, когда соответствующий процесс уже не был запущен. Подтверждение
GUI дано пользователем после перезапуска.

Пройдены 75 Python-тестов, Go-тесты и `go vet`, синтаксическая проверка полного
изменённого frontend через `node --check`. Новые проверки включают реальные ветки
JS с OAuth transport mock: free/pro license, отсутствие лицензии, сохранение
enterprise-ветки. Установка проверяется на сохранение исходного backup при
v1 → v2, rollback при ошибке замены launcher и запрет неявной смены профиля.
Windows-тест с дочерним процессом подтверждает завершение по закрытию Job Object.

Артефакты без OAuth-кодов/заголовков находятся в игнорируемом
`test_cache/gui-aicode-2026-11-15/`: результаты Hub/Manager generation, quota,
installed Manager и manifests. Токены не переносились в исходники или отчёт.

## Восстановление и ограничения

Установщик сохраняет первоначальные `*.before-aicode` и manifest, проверяет
текущие файлы по hash и восстанавливает предыдущие байты при неудачном обновлении.
Ранее существовавшие `.agybak` и frontend `.bak` не переписываются. Команды
`--agy-aicode status|restore --profile cli|hub|manager|ide --target ...` описаны
в README. Для IDE native Hub и frontend восстанавливаются отдельно.

Покрытие ограничено указанными Windows x64-сборками и personal AICode-профилями.
Native quota-переход предназначен для этих профилей; работа enterprise/ADC/API-key
с ним не подтверждена. Другие версии требуют нового аудита. Google определяет
доступные experiences, допуск аккаунта и квоты. Автоматическое обновление native
CLI отключено launcher; обновление самого GUI может заменить backend/frontend.
После обновления приложения нужно повторно проверить статус совместимости.

Предыдущий CLI этап сохранён в `72f9554`. Изменения GUI и квот выделены в
следующий коммит; история находится в Git репозитория.
