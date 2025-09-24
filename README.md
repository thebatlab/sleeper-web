# Sleeper Trades Viewer

A FastAPI web app to view all trades for a given Sleeper username across all leagues.

---

## Run Locally (Docker)

### 1. Pull the image from Docker Hub

```bash
docker pull thebatlab/sleeper-web:latest
```

### 2. Run the container

```bash
docker run -d -p 8000:8000 -e PORT=8000 thebatlab/sleeper-web:latest
```

### 3. Open in a browser

Visit:

```
http://localhost:8000
```

You should see the trade lookup form.

---

## Optional: Rebuild locally

If you want to build from source:

```bash
docker build -t sleeper-web .
docker run -d -p 8000:8000 sleeper-web
```

---

## Run Directly from Source (Python)

1. Create a virtual environment:

```bash
python -m venv venv
source venv/bin/activate  # Linux/macOS
venv\Scripts\activate     # Windows
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run the FastAPI app:

```bash
uvicorn webapp:app --reload --host 0.0.0.0 --port 8000
```

Visit:

```
http://localhost:8000
```

---

## App Features

* Fetches all trades for a Sleeper username across leagues.
* Displays:

  * **Date** (YYYY-MM-DD)
  * **League Name / ID**
  * **Assets Lost**
  * **Assets Gained**
* Trades table is sortable by newest first.
* CSS styling included via `static/style.css`.

---

## Caching & API Notes

* Player data is cached inside the container to reduce Sleeper API calls.
* Trades are fetched live; large queries may take a few seconds.

---

## Contributing / Issues

* Open issues or submit PRs to improve features or performance.
