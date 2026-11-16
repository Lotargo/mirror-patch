# Опциональные Google safety settings в transport launcher

Дата: 2026-11-16. Функция реализована в `source/patcher/agy/aicode_runtime/`.

Native Cascade RPC не передаёт пользовательский массив `safetySettings`.

В том же JSON допускаются серверные секции `tool_calling` и `reasoning`.
Launcher принимает их как opaque bridge options и не отправляет Google:
за них отвечает SDK provider. Google fingerprint по-прежнему учитывает только
четыре provider threshold. Это сохраняет совместимость с runtime options.
Launcher теперь может добавить его на реальной границе исходящего AICode
запроса, после выбора experience из серверной конфигурации.

Функция включается только переменной дочернего процесса
`AGY_GOOGLE_SAFETY_SETTINGS_FILE`, указывающей на JSON:

```json
{
  "google_safety_settings": {
    "HARM_CATEGORY_HARASSMENT": "BLOCK_NONE",
    "HARM_CATEGORY_HATE_SPEECH": "DEFAULT",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT": "DEFAULT",
    "HARM_CATEGORY_DANGEROUS_CONTENT": "DEFAULT"
  }
}
```

Поддержаны четыре категории и пороги Google: unspecified, low/medium/high,
`BLOCK_NONE`, `OFF`. Локальное значение `DEFAULT` оставляет native запрос
неизменным для соответствующей категории. Пропущенные категории также DEFAULT.
При четырёх DEFAULT тело не пересериализуется. При override сохраняются другие
native safety categories/methods и все остальные поля запроса.

Файл читается перед каждым новым Google-запросом. Неверные значения/формат или
недоступный файл вызывают ошибку, а не игнорирование. Размер ограничен 8 KiB.
Google experience определяется по model ID/name из fetchConfig, также по
официальному префиксу chat-gemini. Claude/GPT и другие experiences не меняются.

`--agy-compat-capabilities` возвращает JSON с
`google_safety_settings_file: 1`, без старта native процесса. SDK-сервер проверяет
эту возможность перед включением настройки и при старом launcher выдаёт ошибку.

После ответа upstream рядом создаётся `<settings-file>.receipt.json`:
четыре выбранных значения, SHA256 нормализованного набора, experience,
HTTP-статус и реальный массив `request_safety_settings`. Prompts, model output
и credentials не записываются. Receipt сообщает передачу, не оценку действия
фильтров. Он записывается атомарно; при ошибке записи модельный ответ сохраняется.

Установленный SDK-сервер использует собственную копию launcher и того же native
2.32.1; файлы работающих IDE/Manager не заменялись. По умолчанию настройки не
переопределяются. Native hash check, OAuth, response forwarding, monitors и
разрешения инструментов сохранены.

Проверки: `go test -race ./...`, `go vet ./...`; Python `pytest -q tests` —
84 passed, 28 subtests passed. Полноценный SDK provider выставил все четыре
BLOCK_NONE, Google принял запрос с HTTP 200, модель вернула контрольный маркер.
Сохранение после перезапуска и сброс без массива подтверждены. Последующий
нейтральный контроль встретил 429 и не засчитывается как успешная генерация.

Практическая инструкция находится в SDK:
`F:/projects/antigravity_engine_sdk/docs/GOOGLE-SAFETY-SETTINGS.md`.
