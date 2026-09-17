# HATAO

Complete local hackathon application for EAI39: AI Model Memory Deletion Verification Tool.

## Scope

This is a hackathon prototype using synthetic/approved test data and simulated storage paths. It does not claim to delete or verify data inside ChatGPT, Gemini, Claude, or another third-party AI service.

No API keys, cloud services, LLMs, real vector database, or real personal data are required.

## Stack

- Frontend: HTML, CSS, JavaScript
- Backend: Python + FastAPI
- Database: SQLite

## Run in VS Code

Python 3.10+ recommended.

```bash
python -m venv .venv
```

Windows:
```bash
.venv\Scripts\activate
```

Install:
```bash
pip install -r backend/requirements.txt
```

Start:
```bash
uvicorn backend.main:app --reload --port 8000
```

Open: http://127.0.0.1:8000

SQLite is created automatically as `ai39.db`.

## Main demo

1. Test Chatbot → store synthetic text or test image.
2. See propagation across five simulated stores.
3. Delete memory.
4. Verification engine checks all stores.
5. Inspect completeness, retrieval, residual risk and evidence.
6. Submit human review when residual data is detected.
7. Evaluation tab shows labeled controls, precision and recall.
8. Audit Trail shows timestamped events.
9. Generate a JSON verification report.

## Built-in scenarios

- Complete deletion: 100%, retrieval NO, LOW risk
- Embedding residual: 80%, retrieval YES, MEDIUM risk
- Downstream residual: 80%, retrieval YES, MEDIUM risk
- Multiple residuals: 60%, retrieval YES, HIGH risk

## Tests

```bash
python tests/test_logic.py
```
