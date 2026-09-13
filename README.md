# SelfAD

Personal Attack–Defense platform.

## How to run?

```bash
docker build -t selfad .
docker run --rm -v selfad-data:/app/data -p 8000:8000 selfad
```

The `selfad-data` volume keeps the instance database between container runs.
