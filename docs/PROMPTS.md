# PROMPTS.md

## Versioned prompt set
- `prompts/system.txt` (v1.0.0)
- `prompts/user_template.j2` (v1.0.0)
- `prompts/guidelines.md` (v1.0.0)

## Rendering flow
1. Load `system.txt`
2. Load `guidelines.md`
3. Render `user_template.j2` with context: `mode`, `lang`, `input_text`, `guidelines`
4. Send system/user messages to the LLM client

## Notes
- Templates are file-based and versioned in-file.
- The runtime enforces strict JSON output via Pydantic.
- Prompt injection text from input must be ignored per prompt rules and validator checks.
