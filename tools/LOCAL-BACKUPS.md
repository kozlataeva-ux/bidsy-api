# Локальные зашифрованные backups

Запускать `Local-Backup.ps1` из PowerShell своего обычного Windows account. DPAPI CurrentUser привязывает шифрование к account и его Windows-профилю. Backup, созданный из Codex sandbox, будет привязан к sandbox account. Потеря Windows-профиля, ключей DPAPI или недоступность account создаёт риск невозможности расшифровки. Копирования файла на другой компьютер недостаточно.

```powershell
./tools/Local-Backup.ps1 -Action Save -Source 'C:/path/source.bin'
./tools/Local-Backup.ps1 -Action Verify -Source 'C:/path/backup.bidsy-dpapi'
./tools/Local-Backup.ps1 -Action Restore -Source 'C:/path/backup.bidsy-dpapi' -Destination 'C:/path/restored.bin'
./tools/Test-LocalBackup.ps1
```

Для Save `-Destination` — папка; по умолчанию `.private-backups` рядом с `tools`. Каждый backup имеет уникальное имя. ACL папки разрешает доступ только создающему Windows account и SYSTEM. Не указывайте общую существующую папку: её ACL будет заменён.

Для Restore `-Destination` — обязательный полный путь к новому файлу в существующей папке. Перезапись запрещена. Исходный файл не удаляется. Формат версии 1 и исходное имя находятся внутри зашифрованного payload. Save расшифровывает записанный backup и сверяет байты перед успехом. Verify проверяет расшифровку и структуру. Plaintext byte arrays очищаются в finally; Windows/.NET могут создавать внутренние копии, которые скрипт не контролирует.

Скрипт ничего не загружает, не отправляет и не считывает автоматически. Он работает только с явно указанным файлом. Серверные рабочие credentials остаются на сервере; локальный backup их не отзывает и не заменяет. Для SQLite используйте согласованный backup/экспорт: простая копия активной БД может не включать WAL.
`-ExpectedUserName` можно передать для Save, Verify и Restore: скрипт остановится, если WindowsIdentity.Name не совпадает с указанным account (например, `MY-PC\username`).
