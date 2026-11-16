# Metadata каталога для SDK

Launcher объявляет `model_catalog_file:1` в `--agy-compat-capabilities`.
По явной переменной дочернего процесса `AGY_MODEL_CATALOG_FILE` сохраняется
whitelist metadata успешного `fetchConfig`: experience ID, model ID/name,
display name, supported features/levels и preset thinking settings.
OAuth, headers, prompts и experiment payloads не записываются.

SDK задаёт путь внутри собственной временной папки отдельно каждому процессу;
унаследованный путь не используется. Запись заменяется через временный файл.
Новых запросов/таймеров эта возможность не создаёт: она использует ответ уже
выполненного `fetchConfig`. Сервер обновляет публичный список при старте или
вручную. Инференс, авторизация, разрешения и safety settings не изменяются.

Это устраняет ошибочное исключение моделей вне agent picker, в частности
Flash Lite 3.1. SDK фильтрует FUNCTION_CALLING experience и соединяет их с native
карточками, сохраняя disabled/internal проверки. Список thinking levels позволяет
объявлять только опубликованные Google controls.
