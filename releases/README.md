# Выпуск MirrorPatch

Версия хранится в `source/patcher/constants.py`; PE version resource —
`source/version.txt`. Теги имеют вид `v1.4.0`. Установка из готового EXE не требует
Python и Go; на сборочной машине они нужны.

## Сборка Windows x64

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r source/requirements-build.txt
.\.venv\Scripts\python.exe scripts/build_windows.py
```

Скрипт собирает проверенные Go launchers, упаковывает runtime-исходники и prebuilt
manifest в PyInstaller onefile EXE. Google native EXE в пакет не включаются.
Выходные файлы находятся в игнорируемом `dist/release`:

- `MirrorPatch-windows-x64.exe` — самостоятельный патчер;
- `MirrorPatch-windows-x64.zip` — EXE, README, руководство и лицензии;
- `MirrorPatch-v1.4.0-source.zip` — соответствующий исходный код из Git;
- `SHA256SUMS.txt` — SHA-256 каждого пакета.

Исходный ZIP формируется из текущего commit; перед релизной упаковкой рабочее
дерево должно быть чистым. Сборочный скрипт не публикует файлы и не меняет
установленные приложения.

## Проверка перед публикацией

1. Запустить Python-тесты и `go test ./...`, `go vet ./...`.
2. Проверить готовый EXE через `--version`, `--help`, `--check --json`.
3. На отдельной копии официального бинарника выполнить frozen EXE install,
   status, повторную установку и restore с Go/Python, исключёнными из PATH.
4. Сверить восстановленный файл с исходным, а приложенные SHA-256 — с пакетами.
5. Проверить ZIP на отсутствие пользовательских профилей, логов, токенов и
   бинарников Google. Лицензии зависимостей должны оставаться в ZIP и EXE.

## Публикация

После коммитов кода и оформления отправить `main`, создать тег, загрузить assets
и release notes из `releases/v1.4.0.md`. У выпуска должна быть ссылка на точный
commit. Повторная загрузка assets под существующим тегом не является новым
релизом: исправления выпускаются с новым номером.
