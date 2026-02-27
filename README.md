# gdev-content

> Content Factory MVP — тикет → ответ + summary + action items

Автоматизированный конвейер обработки входящих тикетов с LLM-генерацией контента, quality gate и human-in-the-loop approval.

## Быстрый старт

```bash
# 1. Установить зависимости
pip install anthropic jsonschema

# 2. Настроить переменные
cp .env.example .env
# Заполнить ANTHROPIC_API_KEY, APPROVAL_CHAT_ID, APPROVAL_BOT_TOKEN

# 3. Запустить eval
python eval/run_eval.py --cases eval/cases/ --output eval/results/
```

## Структура

```
gdev-content/
├── docs/
│   ├── PROMPTS.md        — Промпты, схемы, примеры ввода/вывода
│   ├── ARCHITECTURE.md   — Архитектура, n8n workflow, план работ
│   └── EVAL.md           — 20 тест-кейсов, метрики, eval harness
├── prompts/
│   ├── classifier_v1.txt
│   ├── generator_v1.txt
│   ├── quality_gate_v1.txt
│   └── rewriter_v1.txt
├── workflows/
│   └── ticket-to-content-v1.json  — n8n workflow export
├── eval/
│   ├── cases/            — TC-001.json ... TC-020.json
│   ├── results/          — Результаты прогонов
│   └── run_eval.py       — Eval harness
└── .env.example
```

## Pipeline

```
Webhook → Classifier → Generator → Quality Gate → [Rewriter×2] → Human Approval → Delivery
```

## Документация

- [Промпты и примеры](docs/PROMPTS.md)
- [Архитектура и интеграции](docs/ARCHITECTURE.md)
- [Eval и метрики](docs/EVAL.md)
