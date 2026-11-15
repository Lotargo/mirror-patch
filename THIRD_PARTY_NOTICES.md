# Сторонние компоненты и происхождение

## Исходная база патчера

MirrorPatch основан на snapshot **Open AG Patcher 1.3.8** из
[AvenCores/open-antigravity-patcher](https://github.com/AvenCores/open-antigravity-patcher).
Импорт зафиксирован в истории Git коммитом `f069fdf`.
Исходный проект распространяется под GPL-3.0; эта лицензия сохранена в [LICENSE](LICENSE).
Дополнения MirrorPatch также распространяются под GPL-3.0.

Исходный проект указывает использование наработок
[QNIX-Dev/eligibility-antigravity-patcher](https://github.com/QNIX-Dev/eligibility-antigravity-patcher)
под MIT. [Текст лицензии и copyright](licenses/eligibility-antigravity-patcher-MIT.txt)
включён в репозиторий и пакет. Изменение брендинга и удаление рекламных
ссылок не отменяет лицензии и авторских прав исходных компонентов.

## Готовая Windows-сборка

В EXE входят Python runtime и зависимости, упакованные PyInstaller.
Python распространяется по PSF License; PyInstaller — по GPL-2.0 с исключением
для bootloader; `packaging` — по Apache-2.0/BSD-2-Clause. Их лицензии сохраняют
свои условия. Соответствующий исходный код патчера и его Go launchers доступен
в теге выпуска; инструкция сборки — в `releases/README.md`.

## Приложения Google

Бинарники Antigravity и их код не распространяются в assets MirrorPatch.
Патчер работает с установленными пользователем файлами; при необходимости
получает строго проверенную CLI-сборку непосредственно с официального сервера.
Названия Antigravity, Gemini и AICode принадлежат их правообладателям.
