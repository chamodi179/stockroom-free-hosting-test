# Stockroom (FastAPI + MySQL)

Login, products, orders and a dashboard, backed by MySQL.

## Run
1. Have MySQL 8 running and an account that can create a database.
2. `python -m venv .venv && source .venv/bin/activate`
3. `pip install -r requirements.txt`
4. Set the variables from `.env.example` in your shell, e.g.
   `export $(grep -v '^#' .env.example | xargs)` after editing it.
5. `uvicorn main:app --reload`
6. Open http://127.0.0.1:8000 and sign in with `demo@example.com` / `demo1234`.

On first start the app creates the database, the tables (`schema.sql`) and demo data.

## Before going live
- Set a long random `SECRET_KEY` and `HTTPS_ONLY=1` behind HTTPS.
- Use a dedicated MySQL user with rights on this database only.
- Change or delete the demo account.
