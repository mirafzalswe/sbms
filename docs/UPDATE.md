# Обновление проекта на сервере (через GitHub)

Пошаговая инструкция: как после правок кода на Mac выкатить их на сервер `164.92.174.249`.

---

## Схема

```
┌──────────┐   git push    ┌──────────┐   git pull    ┌──────────┐
│   Mac    │ ───────────► │  GitHub  │ ───────────► │  Сервер  │
└──────────┘               └──────────┘               └──────────┘
                                                      restart systemd
```

Правишь локально → пушишь в GitHub → подтягиваешь на сервер → рестартишь сервис.

---

## 1. На Mac — запушить изменения в GitHub

```bash
cd /Users/Macbook/Desktop/sbms_test

# 1. Посмотреть, что изменилось
git status

# 2. Добавить все изменения
git add .

# 3. Убедиться, что .env НЕ идёт в коммит (должен быть в .gitignore)
git status | grep -i "\.env"
# если видишь .env в staged — СТОП, добавь в .gitignore и повтори git add

# 4. Коммит с осмысленным сообщением
git commit -m "добавил X / починил Y"

# 5. Отправить на GitHub
git push
```

Если просит логин/пароль — ввод username `mirafzalswe` + token (`ghp_...`).

---

## 2. На сервере — подтянуть изменения

### Вариант А — через SSH с Mac одной командой

```bash
ssh mirafzal@164.92.174.249 "cd ~/apps/sbms && git pull && sudo systemctl restart sbms-test"
```

Готово — новая версия уже работает.

### Вариант Б — зайти на сервер и руками

```bash
# на Mac
ssh mirafzal@164.92.174.249

# на сервере
cd ~/apps/sbms
git pull
```

Возможные варианты вывода `git pull`:

| Вывод | Что значит |
|-------|-----------|
| `Already up to date.` | Ничего не поменялось с прошлого pull |
| `Fast-forward ... X files changed` | Обновилось успешно |
| `error: Your local changes ... would be overwritten` | На сервере есть правки — см. раздел "Конфликты" ниже |
| `Authentication failed` | Токен протух — обнови remote URL (см. ниже) |

Если обновились `.py` файлы или `server.py` — перезапусти сервис:

```bash
sudo systemctl restart sbms-test
sudo systemctl status sbms-test --no-pager
```

Если поменялся `requirements.txt` (добавлена новая библиотека):

```bash
.venv/bin/pip install -r requirements.txt
sudo systemctl restart sbms-test
```

Если поменялся только `.html` / `.css` / `.js` — **рестарт не нужен**, Flask отдаёт статику на лету. Обнови страницу в браузере.

---

## 3. Проверить что обновилось

```bash
# последние коммиты на сервере
git log --oneline -5

# статус сервиса
sudo systemctl status sbms-test --no-pager

# живой лог (Ctrl+C для выхода)
sudo journalctl -u sbms-test -f
```

В браузере: http://164.92.174.249/ — должна быть новая версия.

---

## 4. Конфликты при pull (если правил на сервере)

Если на сервере случайно изменил файлы и `git pull` ругается:

### Вариант 1 — откатить правки на сервере (проще)

```bash
cd ~/apps/sbms
git stash           # временно убрать локальные правки
git pull            # подтянуть с GitHub
git stash drop      # удалить сохранённые правки (необратимо)
```

### Вариант 2 — сохранить правки с сервера в ветку

```bash
git stash
git pull
git stash pop       # вернуть правки поверх новой версии
# дальше решить конфликты вручную в nano
```

**Совет:** НЕ редактируй код на сервере. Только на Mac → push → pull.

---

## 5. Токен GitHub протух (через 90 дней)

Если при `git pull` видишь `Authentication failed`:

1. На Mac: https://github.com/settings/tokens → сгенерируй новый токен
2. На сервере обнови URL с новым токеном:

```bash
cd ~/apps/sbms
git remote set-url origin https://mirafzalswe:ghp_НОВЫЙ_ТОКЕН@github.com/mirafzalswe/sbms.git
git pull
```

---

## 6. Полезные команды на сервере

```bash
# Рестарт приложения
sudo systemctl restart sbms-test

# Остановить / запустить
sudo systemctl stop sbms-test
sudo systemctl start sbms-test

# Логи последние 50 строк
sudo journalctl -u sbms-test -n 50 --no-pager

# Логи живые
sudo journalctl -u sbms-test -f

# Статус
sudo systemctl status sbms-test

# Рестарт nginx (если правил /etc/nginx/...)
sudo systemctl reload nginx

# Что запущено на порту 5000
sudo ss -tulpn | grep 5000

# Свободная память / диск
free -h
df -h
```

---

## 7. Если сервис не стартует после pull

```bash
# посмотреть ошибку
sudo journalctl -u sbms-test -n 100 --no-pager

# частые причины:
# 1. Синтаксическая ошибка в .py
.venv/bin/python -c "import server"

# 2. Не хватает новой библиотеки
.venv/bin/pip install -r requirements.txt

# 3. .env не читается
cat .env                    # проверь, что файл есть
sudo systemctl cat sbms-test | grep EnvironmentFile

# 4. Порт 5000 занят
sudo ss -tulpn | grep 5000
```

---

## 8. Быстрая шпаргалка (минимум команд)

**На Mac (после правок):**
```bash
cd /Users/Macbook/Desktop/sbms_test
git add . && git commit -m "update" && git push
```

**На сервере (деплой):**
```bash
ssh mirafzal@164.92.174.249 "cd ~/apps/sbms && git pull && sudo systemctl restart sbms-test"
```

**Проверить:**
- http://164.92.174.249/
- `ssh mirafzal@164.92.174.249 "sudo systemctl status sbms-test"`

---

## 9. Автоматизация (опционально)

Если надоест каждый раз писать ssh-команду — добавь alias на Mac в `~/.zshrc`:

```bash
alias sbms-deploy='cd /Users/Macbook/Desktop/sbms_test && git push && ssh mirafzal@164.92.174.249 "cd ~/apps/sbms && git pull && sudo systemctl restart sbms-test"'
alias sbms-logs='ssh mirafzal@164.92.174.249 "sudo journalctl -u sbms-test -f"'
alias sbms-status='ssh mirafzal@164.92.174.249 "sudo systemctl status sbms-test --no-pager"'
```

Применить:
```bash
source ~/.zshrc
```

Теперь правишь код → `git commit -m "..."` → `sbms-deploy` и всё готово.
