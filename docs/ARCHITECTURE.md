# ARCHITECTURE.md — gdev-content MVP
> v0.1 · 2026-02-27

---

## 1. Обзор системы

**gdev-content** — это конвейер автоматической обработки входящих тикетов/запросов с генерацией структурированного контента, quality gate и human-in-the-loop approval перед публикацией.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          gdev-content PIPELINE                              │
│                                                                             │
│  ┌──────────┐    ┌───────────┐    ┌────────────┐    ┌──────────────────┐   │
│  │  INPUT   │───▶│CLASSIFIER │───▶│ GENERATOR  │───▶│  QUALITY GATE   │   │
│  │          │    │  (LLM)    │    │   (LLM)    │    │    (LLM judge)  │   │
│  │ - ticket │    │           │    │            │    │                  │   │
│  │ - webhook│    │ type,     │    │user_reply  │    │ 8-point check   │   │
│  │ - form   │    │ urgency,  │    │team_summary│    │ score ≥ 0.875   │   │
│  │          │    │ language, │    │action_items│    │                  │   │
│  └──────────┘    │ pii_flag  │    │translation?│    └────────┬─────────┘   │
│                  └───────────┘    └────────────┘             │             │
│                                                    ┌──────────▼──────────┐  │
│                                                    │   APPROVE or REJECT │  │
│                                                    └──────┬────────┬─────┘  │
│                                                    REJECT │        │ APPROVE│
│                                              ┌───────────▼─┐      │        │
│                                              │  REWRITER   │      │        │
│                                              │  (max 2x)   │      │        │
│                                              └──────┬──────┘      │        │
│                                                     │ retry QA    │        │
│                                                     └─────────────┘        │
│                                                                             │
│                                                  ┌──────────────────────┐  │
│                                                  │  HUMAN APPROVAL UI   │  │
│                                                  │  (Telegram / Slack)  │  │
│                                                  └────────┬─────────────┘  │
│                                             APPROVED │    │ REJECTED        │
│                                          ┌────────────▼┐  └──▶ archive     │
│                                          │   DELIVERY  │                   │
│                                          │  (stub/real)│                   │
│                                          └─────────────┘                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Компоненты

### 2.1 Оркестратор: n8n

n8n выбран как основной оркестратор по следующим причинам:
- Self-hosted (контроль над данными — важно для PII)
- Визуальный workflow + версионирование через git export
- Нативные HTTP, Webhook, Telegram, Slack, Email ноды
- Бесплатный open-source для MVP

**Альтернатива:** Make.com (если нет self-host сервера) — аналогичный сценарий через HTTP modules.

### 2.2 LLM Backend: Anthropic Claude API

- Classifier: `claude-haiku-4-5` (быстро, дёшево, JSON-mode)
- Generator: `claude-sonnet-4-6` (качество ответов)
- Quality Gate: `claude-sonnet-4-6` (точность оценки)
- Rewriter: `claude-sonnet-4-6`

Все запросы через единый HTTP-узел n8n → Anthropic API endpoint.

### 2.3 Storage: PostgreSQL (или SQLite для MVP)

```sql
-- Основные таблицы

tickets (
  id          TEXT PRIMARY KEY,
  raw_text    TEXT,
  submitted_at TIMESTAMPTZ,
  user_name   TEXT,
  status      TEXT  -- 'new'|'processing'|'qa_passed'|'approved'|'delivered'|'rejected'
)

pipeline_runs (
  id          SERIAL PRIMARY KEY,
  ticket_id   TEXT REFERENCES tickets(id),
  stage       TEXT,  -- 'classifier'|'generator'|'quality_gate'|'rewriter'|'human'
  attempt     INT DEFAULT 1,
  input_json  JSONB,
  output_json JSONB,
  created_at  TIMESTAMPTZ DEFAULT NOW()
)

approvals (
  id          SERIAL PRIMARY KEY,
  ticket_id   TEXT,
  reviewer    TEXT,
  decision    TEXT,  -- 'approved'|'rejected'
  comment     TEXT,
  decided_at  TIMESTAMPTZ
)
```

### 2.4 Human Approval Interface

**MVP: Telegram Bot**

Бот отправляет карточку с:
- Краткое summary тикета
- user_reply (preview)
- action_items список
- Кнопки: ✅ APPROVE / ❌ REJECT / ✏️ EDIT & APPROVE

При REJECT → поле для комментария → feedback идёт обратно в Rewriter.
При EDIT & APPROVE → inline редактирование текста прямо в Telegram.

**Production upgrade:** Web UI (React + REST API) или Slack Block Kit workflow.

---

## 3. n8n Workflow — подробное описание

### Workflow: `ticket-to-content-v1`

```
[Webhook: POST /webhook/ticket]
         │
         ▼
[Set Variables]  ← ticket_id, submitted_at, user_name, ticket_text
         │
         ▼
[HTTP Request: Classifier]
POST https://api.anthropic.com/v1/messages
Headers: x-api-key, anthropic-version
Body: { model, max_tokens, system: {{classifier_system}}, messages: [...] }
         │
         ▼
[Parse JSON: classifier_result]
         │
         ├─── pii_detected=true → [Notify: PII Alert] → [Stop & Archive]
         │
         ├─── sensitive_topic=true → [Flag for Manual Review] → [Notify Reviewer]
         │
         └─── normal flow ↓
         │
         ▼
[HTTP Request: Generator]
(inject classifier_result fields into generator template)
         │
         ▼
[Parse JSON: generator_result]
         │
         ▼
[Set: attempt = 1]
         │
         ▼
[HTTP Request: Quality Gate]
         │
         ▼
[Parse JSON: qa_result]
         │
         ├─── verdict=APPROVE → [Go to: Human Approval]
         │
         └─── verdict=REJECT ↓
                   │
                   ▼
              [IF: attempt < 3]
                   │ YES          │ NO
                   ▼              ▼
          [HTTP: Rewriter]   [Escalate to Human]
                   │
                   ▼
          [Increment attempt]
                   │
                   └──────────── back to [Quality Gate]
         │
         ▼
[Human Approval: Telegram Bot]
Send formatted message with InlineKeyboard
Wait for callback (webhook or polling)
         │
         ├─── APPROVE →
         │         [Update DB: status=approved]
         │         [Delivery: send to destination]
         │         [Log to pipeline_runs]
         │
         └─── REJECT →
                   [Save reviewer comment]
                   [Trigger Rewriter with comment as context]
                   [Re-enter pipeline at Quality Gate]
```

### n8n Workflow JSON Export

Файл: `workflows/ticket-to-content-v1.json`

```json
{
  "name": "ticket-to-content-v1",
  "nodes": [
    {
      "id": "webhook-entry",
      "type": "n8n-nodes-base.webhook",
      "name": "Ticket Webhook",
      "parameters": {
        "path": "ticket",
        "httpMethod": "POST",
        "responseMode": "responseNode"
      }
    },
    {
      "id": "set-vars",
      "type": "n8n-nodes-base.set",
      "name": "Set Variables",
      "parameters": {
        "values": {
          "string": [
            { "name": "ticket_id",    "value": "={{ $json.ticket_id || 'TKT-' + Date.now() }}" },
            { "name": "ticket_text",  "value": "={{ $json.text }}" },
            { "name": "user_name",    "value": "={{ $json.user_name || 'User' }}" },
            { "name": "submitted_at", "value": "={{ new Date().toISOString() }}" }
          ]
        }
      }
    },
    {
      "id": "classifier-llm",
      "type": "n8n-nodes-base.httpRequest",
      "name": "Classifier LLM",
      "parameters": {
        "url": "https://api.anthropic.com/v1/messages",
        "method": "POST",
        "headers": {
          "x-api-key": "={{ $env.ANTHROPIC_API_KEY }}",
          "anthropic-version": "2023-06-01",
          "content-type": "application/json"
        },
        "body": {
          "model": "claude-haiku-4-5-20251001",
          "max_tokens": 256,
          "system": "{{ $node['Load Prompts'].json.classifier_system }}",
          "messages": [
            {
              "role": "user",
              "content": "Classify the following ticket.\n\n---TICKET---\n{{ $json.ticket_text }}\n---END---"
            }
          ]
        }
      }
    },
    {
      "id": "generator-llm",
      "type": "n8n-nodes-base.httpRequest",
      "name": "Generator LLM",
      "parameters": {
        "url": "https://api.anthropic.com/v1/messages",
        "method": "POST",
        "headers": {
          "x-api-key": "={{ $env.ANTHROPIC_API_KEY }}",
          "anthropic-version": "2023-06-01"
        },
        "body": {
          "model": "claude-sonnet-4-6",
          "max_tokens": 2048,
          "system": "{{ $node['Load Prompts'].json.generator_system }}",
          "messages": [{ "role": "user", "content": "={{ $node['Build Generator Prompt'].json.prompt }}" }]
        }
      }
    },
    {
      "id": "quality-gate-llm",
      "type": "n8n-nodes-base.httpRequest",
      "name": "Quality Gate LLM"
    },
    {
      "id": "rewrite-loop",
      "type": "n8n-nodes-base.if",
      "name": "Need Rewrite?",
      "parameters": {
        "conditions": {
          "string": [
            { "value1": "={{ $json.verdict }}", "operation": "equals", "value2": "REJECT" }
          ],
          "number": [
            { "value1": "={{ $node['Counter'].json.attempt }}", "operation": "smallerEqual", "value2": 2 }
          ]
        }
      }
    },
    {
      "id": "telegram-approval",
      "type": "n8n-nodes-base.telegram",
      "name": "Human Approval Request",
      "parameters": {
        "chatId": "={{ $env.APPROVAL_CHAT_ID }}",
        "text": "={{ $node['Format Approval Message'].json.message }}",
        "additionalFields": {
          "parse_mode": "Markdown",
          "reply_markup": {
            "inline_keyboard": [[
              { "text": "✅ APPROVE", "callback_data": "approve:{{ $json.ticket_id }}" },
              { "text": "❌ REJECT",  "callback_data": "reject:{{ $json.ticket_id }}" }
            ]]
          }
        }
      }
    },
    {
      "id": "delivery-stub",
      "type": "n8n-nodes-base.httpRequest",
      "name": "Delivery Stub",
      "parameters": {
        "url": "={{ $env.DELIVERY_WEBHOOK_URL }}",
        "method": "POST",
        "body": "={{ $json }}"
      }
    }
  ],
  "connections": {
    "Ticket Webhook":        { "main": [["Set Variables"]] },
    "Set Variables":         { "main": [["Classifier LLM"]] },
    "Classifier LLM":        { "main": [["Generator LLM"]] },
    "Generator LLM":         { "main": [["Quality Gate LLM"]] },
    "Quality Gate LLM":      { "main": [["Need Rewrite?"]] },
    "Need Rewrite? (true)":  { "main": [["Rewriter LLM"]] },
    "Need Rewrite? (false)": { "main": [["Human Approval Request"]] },
    "Rewriter LLM":          { "main": [["Quality Gate LLM"]] },
    "Human Approval Request":{ "main": [["Delivery Stub"]] }
  }
}
```

---

## 4. Make.com — альтернативный вариант

Если нет self-hosted сервера — аналогичный сценарий на Make.com:

```
Trigger: Webhook (Custom)
  └─▶ HTTP: POST /v1/messages (Classifier)
       └─▶ Router: pii? / sensitive? / normal
            └─▶ HTTP: POST /v1/messages (Generator)
                 └─▶ HTTP: POST /v1/messages (QA Gate)
                      └─▶ Iterator + Aggregator (retry loop)
                           └─▶ Telegram: Send approval card
                                └─▶ Webhook: Wait for callback
                                     └─▶ HTTP: Delivery stub
```

---

## 5. Переменные окружения

```env
# .env (никогда не коммитить!)
ANTHROPIC_API_KEY=sk-ant-...
APPROVAL_CHAT_ID=-100...          # Telegram chat/group ID
APPROVAL_BOT_TOKEN=...            # Telegram bot token
DELIVERY_WEBHOOK_URL=https://...  # куда отправлять approved output
N8N_HOST=localhost
N8N_PORT=5678
DB_URL=postgresql://user:pass@localhost:5432/gdev_content
```

---

## 6. Delivery destinations (stubs)

| Destination | Тип | Статус MVP |
|-------------|-----|-----------|
| Telegram group | Бот-сообщение | ✅ Реализован |
| Slack channel | Incoming Webhook | ✅ Stub (1 HTTP call) |
| Email (SMTP) | n8n Email node | ✅ Stub |
| CRM / Helpdesk | REST API | 🔲 Roadmap |
| Jira ticket | REST API | 🔲 Roadmap |

---

## 7. Безопасность

| Риск | Митигация |
|------|-----------|
| Утечка API ключа | Env vars, никогда в коде/логах |
| PII в логах | Classifier фильтрует до логирования; pipeline_runs.input_json маскирует PII поля |
| Prompt injection | Входящий текст всегда в явных тегах `---TICKET---...---END---` |
| Неограниченный retry | Max 2 rewrite attempts, затем escalation |
| Неавторизованный approve | Approval только через зарегистрированные Telegram user IDs |

---

## 8. Roadmap после MVP

```
v0.1 (MVP)   ─ Текущий: n8n + Claude API + Telegram approval
v0.2         ─ Web UI для approval + history
v0.3         ─ Metrics dashboard (Grafana) + alert на деградацию качества
v0.4         ─ Multi-game context (разные системные промпты per game)
v0.5         ─ Fine-tuned classifier на накопленных данных
v1.0         ─ Production: Kubernetes, audit log, SOC2-ready
```

---

## 9. План работ (2–4 вечера)

| Вечер | Задачи | Результат |
|-------|--------|-----------|
| **1** | Настройка n8n (Docker Compose), создание промптов v1, ручной тест через curl | Работающий pipeline без UI |
| **2** | Telegram bot approval, quality gate loop, 3 live тест-прогона | Full pipeline с human-in-the-loop |
| **3** | Eval harness (20 кейсов), метрики, README, запись демо | Готовое демо для работодателя |
| **4** | Buffer: polish, edge cases, Make.com mirror, slides | Запасное время |

### Что показать работодателю

1. **Live demo**: отправить тикет → через 15 сек — approval в Telegram → нажать approve → показать delivered JSON
2. **Prompts as a product**: открыть `docs/PROMPTS.md` — версии, схемы, guardrails
3. **Quality Gate в действии**: показать кейс где QA reject → rewrite → approve
4. **Eval отчёт**: `docs/EVAL.md` с метриками по 20 кейсам
5. **Архитектурная схема**: этот файл

---

*Документ обновляется при каждом изменении workflow. Экспорт n8n JSON коммитится в `workflows/`.*
