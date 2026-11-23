# Лечение Hub backend после официального автообновления (2026-11-24)

## Симптом

`./check_and_patch.ps1`, в том числе с `-Force`, завершался кодом 2:

```text
[UNSUPPORTED] Antigravity Hub backend
    Path: $env:USERPROFILE\.gemini\bin\agy.exe
    Mode: hub; file state: unknown
    Hub auth diagnostics are read-only; CLI markers do not establish successful Hub login.
[!] Local patch coverage is incomplete: Antigravity Hub backend
```

## Причина

Официальный автоапдейтер IDE 19.11.2026 перезаписал `agy.exe` в `$env:USERPROFILE\.gemini\bin` сборкой 1.5.11 (234827416 байт). Это был лаунчер patched-совместимости, поэтому:

- `agy-aicode-patch.json` остался целым, `profile: hub`, `patch_version: 4`;
- `agy-aicode-native.exe` остался проверенным нативным патчем 1.5.9 (`8c033def...`);
- `agy.exe.before-aicode` остался исходным бинарником (`834c15e6...`);
- хеш самого `agy.exe` перестал совпадать с `launcher_sha256` из манифеста.

`hub_auth` зависит от правильного лаунчера, поэтому профиль стал `unsupported`, а стандартное лечение Desktop Helper вообще отказывалось писать в неизвестный бинарник.

## Изменение

В `source/patcher/desktop_helper.py` добавлено распознавание подтверждённого автообновления:

- sidecar `agy.version.json` принимается только при `schemaVersion == 1`, версии вида `x.y.z`, хосте `antigravity-cli-auto-updater-974169037036.us-central1.run.app` в `releaseBaseUrl`, совпадении `size` и SHA-512 с текущим `agy.exe`;
- такой бинарник помечается как `modified` с `can_heal = true`, detail показывает версию обновления;
- перед лечением текущий `agy.exe` и его sidecar сохраняются в `*.before-heal-<version>-<timestamp>`;
- лаунчер пересобирается из уже проверенного нативного компаньона, манифест обновляется;
- sidecar синхронизируется по размеру, mtime, inode и SHA-512 нового лаунчера, версия остаётся 1.5.11, чтобы IDE не скачала 1.5.11 повторно;
- неподтверждённые или поддельные сборки по-прежнему отклоняются без записи.

Добавлены тесты в `tests/test_desktop_helper.py`: лечение подтверждённого обновления с recovery и синхронизацией sidecar, отказ и сохранение файла при чужом `releaseBaseUrl`.

## Проверка

- `pytest tests -q`: 90 passed, 28 subtests passed.
- `python source/main.py --desktop-patcher heal --module ide`: `ide_hub` = `applied`, итоговый статус модуля `patched`.
- `python source/main.py --agy-aicode status --profile hub --target ...`: `state: installed`, все три проверки `true`.
- `./check_and_patch.ps1 -Force -NoNotify`: все обязательные компоненты `PATCHED`, `EXITCODE=0`.

## Откат

Восстановить официальную сборку 1.5.11 из `agy.exe.before-heal-1.5.11-*` и соответствующий `agy.version.json.before-heal-1.5.11-*`. Для возврата к непатченному legacy-бинарнику, который был до установки AICode, использовать `--agy-aicode restore --profile hub --target $env:USERPROFILE\.gemini\bin\agy.exe`.
