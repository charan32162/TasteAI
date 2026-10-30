# TasteAI local demo

This demo uses the explicitly fictional seven-dish catalog. It demonstrates working deterministic product paths; it does not demonstrate live restaurant menu evidence, OCR, LLM, or vector search.

## Start

```powershell
python -m venv tasteai\.venv
.\tasteai\.venv\Scripts\Activate.ps1
python -m pip install -r tasteai\requirements-dev.txt
$env:TASTEAI_SECRET_KEY = (python -c "import secrets; print(secrets.token_urlsafe(48))")
python -m uvicorn tasteai.app:app --reload --port 8000
```

Open `http://127.0.0.1:8000`.

## Suggested walkthrough

1. As a guest, search `spicy chicken under ₹300`; inspect the parsed intent, hard price filter, match components, and grounded explanation.
2. Submit `Never recommend paneer` to `POST /api/v1/agent/chat` without a token. The deterministic demo response works, but no personal memory is stored.
3. Create an account and repeat the chat request. The explicit preference is saved to that account's food memory; inspect it at `GET /api/v1/food-memory`.
4. Use food feedback on a recommendation and view the resulting history or updated Taste DNA.
5. Try `peanut allergy`; the demo catalog cannot verify it, so recommendations fail closed.
6. Open restaurant discovery. With provider configuration disabled, the UI reports availability without requesting browser location or inventing real restaurants.
7. Review the API documentation for group planning, taste transfer, and the reviewer-only menu-photo proposal flow. The photo route requires an existing canonical restaurant identity and a reviewer allowlist; no sample real menu data is included.

## Verify offline behavior

```powershell
python -m unittest discover -s tasteai\tests -v
python -m tasteai.evaluation.run --strict
```

The suite uses mocks and temporary SQLite databases. The optional live Google smoke test is skipped unless its separate opt-in and all provider gates are enabled.
