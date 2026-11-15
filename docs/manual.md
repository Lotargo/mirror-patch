# Руководство MirrorPatch

## Готовый Windows EXE

Скачайте `MirrorPatch-windows-x64.exe` из Releases или распакуйте ZIP. Python и
Go для готовой сборки не нужны. Закройте Antigravity и запущенные `agy`, затем
выберите **13 — AICode access + quotas**. В подменю можно установить профиль для
IDE с Hub, Manager, обычного CLI либо всех найденных приложений.

Для пользовательских установок в AppData достаточно обычного запуска. Если
папка защищена от записи, используйте «Запуск от имени администратора».
Автоматического запроса повышения прав при обычном запуске нет.

Нужны установленные официальные приложения. Запустите их хотя бы один раз,
чтобы был скачан backend IDE. Проверяемое покрытие: IDE 2.7.0, CLI/Hub 1.5.9 и
Manager 2.32.1 на Windows x64, personal Google-аккаунт. Другие сборки отклоняются
по hash до записи. Использование `--source` не отключает эту проверку.

## Установка из командной строки

```powershell
$hub = Join-Path $env:USERPROFILE '.gemini/bin/agy.exe'
$ide = Join-Path $env:LOCALAPPDATA 'Programs/Antigravity IDE/resources/app/out/main.js'
$manager = Join-Path $env:LOCALAPPDATA 'Programs/antigravity/resources/bin/language_server.exe'
$cli = (Get-Command agy).Source

.\MirrorPatch-windows-x64.exe --agy-aicode install --profile hub --target "$hub"
.\MirrorPatch-windows-x64.exe --agy-aicode install --profile ide --target "$ide"
.\MirrorPatch-windows-x64.exe --agy-aicode install --profile manager --target "$manager"
.\MirrorPatch-windows-x64.exe --agy-aicode install --profile cli --target "$cli"
```

`--source PATH` необязателен. Сначала проверяются целевой EXE и его сохранённые
оригиналы. Для CLI/Hub при необходимости скачивается официальная 1.5.9 с сервера
Google; размер 231726744 байта и SHA-512 проверяются до установки. Копия попадает
в `%LOCALAPPDATA%/MirrorPatch/cache`. Если нужный оригинал Manager отсутствует,
патчер попросит предоставить проверенную сборку, а не заменит её другим продуктом.

В готовом EXE находятся четыре собственных prebuilt launcher для известных
native-хешей. Их manifest проверяется перед копированием. Бинарники Google в EXE
не упакованы. При запуске из исходников launcher собирается локальным Go.

Установка исправляет только файлы выбранных профилей. Не все прежние ошибки
сервера означают неисправность патча: допуск аккаунта, квоты и доступные модели
по-прежнему определяет Google. Если нужна новая OAuth-сессия, завершите вход в
браузере самостоятельно. Коды и токены не нужно публиковать в Issues.

## Проверка

```powershell
.\MirrorPatch-windows-x64.exe --version
.\MirrorPatch-windows-x64.exe --check --json
.\MirrorPatch-windows-x64.exe --hub-auth-status --repeat 2 --json
.\MirrorPatch-windows-x64.exe --agy-aicode status --profile hub --target "$hub"
.\MirrorPatch-windows-x64.exe --agy-aicode status --profile ide --target "$ide"
```

`--check` проверяет локальные файлы. `--hub-auth-status` читает состояние у
работающего Hub и требует запущенную IDE. Самостоятельный Hub без frontend может
быть не авторизован: сессию ему передаёт приложение. Ни одна из этих команд не
заменяет проверку реального ответа модели. После установки выполните короткий
запрос и откройте **Settings → Models & Usage**.

CLI принимает тот же `--model`, что официальный клиент. Точное имя experience
предпочтительно. Неоднозначный model ID или отсутствующий явный вариант дают
ошибку со списком доступных experience из `fetchConfig`. В коде нет обязательного
Flash Low; модель на скриншоте или в примере не ограничивает остальные варианты.

## Резервные копии и откат

Native-профиль сохраняет рядом с EXE:

- `имя.exe.before-aicode` — файл, установленный до первого применения AICode;
- `agy-aicode-native.exe` — исправленный native-компонент;
- `agy-aicode-patch.json` — версии, профили и проверяемые хеши.

Для frontend сохраняются `main.js.before-aicode` и `main.js.aicode.json`.
Исторические `.agybak` и `.bak` не перезаписываются. При обновлении v1 → v2
первоначальный backup остаётся прежним; сбой замены возвращает предыдущие файлы.
Не удаляйте companion/manifest вручную, пока используется launcher.

Закройте приложение и восстановите нужный профиль в пункте 13 или командой:

```powershell
.\MirrorPatch-windows-x64.exe --agy-aicode restore --profile manager --target "$manager"
.\MirrorPatch-windows-x64.exe --agy-aicode restore --profile ide --target "$ide"
.\MirrorPatch-windows-x64.exe --agy-aicode restore --profile hub --target "$hub"
.\MirrorPatch-windows-x64.exe --agy-aicode restore --profile cli --target "$cli"
```

Для IDE восстанавливаются `ide` и `hub`. Перед ручным `agy update` восстановите
native-файл. Автообновление native отключено в окружении дочернего процесса;
обновление самого GUI способно заменить backend или frontend. После обновления
приложения снова проверьте совместимость версии и hash.

## Разработка и сборка

Для исходников: Python 3.11+ и Go 1.23+. Установите `packaging`, запустите
`python source/main.py`. Команды EXE также доступны через эту точку входа.

```powershell
python -m unittest discover -s tests -v
Push-Location source/patcher/agy/aicode_runtime
go test ./...
go vet ./...
Pop-Location
```

Порядок создания самостоятельного Windows EXE описан в
[releases/README.md](../releases/README.md). Старые пункты 1–12 сохранены для
legacy-профилей и диагностики. Они не являются установкой нового AICode-пути.
