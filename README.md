# SelfAD

Personal Attack–Defense platform.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Open <http://localhost:8000>. Health check: <http://localhost:8000/health>.

## Docker

```bash
docker build -t selfad .
docker run --rm -v selfad-data:/app/data -p 8000:8000 selfad
```

The `selfad-data` volume keeps the instance database between container runs.
