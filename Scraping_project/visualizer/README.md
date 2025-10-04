# 📊 Pipeline Visualizer 📊

Welcome to the real-time dashboard for the UConn Web Scraping Pipeline! This tool brings your data to life, letting you watch the entire scraping process unfold in your browser. 🚀

---

## ✨ Features

- **🌐 Live Network Graph:** A stunning D3.js force-directed graph shows how URLs are discovered and connected.
- **📈 Real-Time Statistics:** Keep an eye on key metrics like processing rates and content types.
- **🚦 Status Code Distribution:** Instantly see the health of the URLs you're scraping.
- **📜 Event Log Stream:** A live feed of every event happening in the pipeline.

---

## 🚀 How to Use

Getting started is as easy as 1-2-3!

### 1. Install Dependencies

```bash
pip install fastapi uvicorn websockets
```

### 2. Start the Visualizer Server

```bash
cd Scraping_project/visualizer
python server.py
```

### 3. Open Your Browser

Navigate to [http://localhost:8080](http://localhost:8080) to see the magic happen!

### 4. Run the Pipeline

The pipeline will automatically connect to the visualizer. Just run it as usual:

```bash
cd Scraping_project
python -m src.orchestrator.main --env development --stage all
```

---

## 🛠️ How It Works

The visualizer works by listening for events from the scraper.

```
Scraper → POST /event → FastAPI Server → WebSocket /ws → Browser ✨
```

1.  The scraper sends an event (e.g., `url_discovered`).
2.  The FastAPI server catches the event.
3.  The server broadcasts the event over a WebSocket.
4.  The frontend updates the graphs and stats in real-time!

---

## 🔧 API Endpoints

- **`GET /health`**: A simple health check to see if the server is running.
- **`POST /event`**: The endpoint where the scraper sends event data.
- **`/ws`**: The WebSocket that streams events to the browser.

---

## 🎨 Customization

Make the visualizer your own! All the files you need are in the `visualizer/static/` directory.

- **Styling:** Change the look and feel by editing `style.css`.
- **Graph Layout:** Adjust the graph's physics in `script.js`.

---

## 🚢 Deployment

Ready to take your visualizer to production? Here are your options.

### Gunicorn

For a robust, multi-worker setup:

```bash
pip install gunicorn
gunicorn visualizer.server:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8080
```

### Docker

Containerize the visualizer for easy deployment:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY visualizer/ /app/visualizer/
RUN pip install fastapi uvicorn websockets
EXPOSE 8080
CMD ["uvicorn", "visualizer.server:app", "--host", "0.0.0.0", "--port", "8080"]
```

---

## 🤯 Troubleshooting

- **Connection Failed?**
  - Make sure the server is running and that port 8080 is not blocked.

- **No Events?**
  - Check that `METRICS_ENABLED` is `True` in `src/common/constants.py`.

- **Performance Issues?**
  - For very large crawls, you can adjust the graph settings in `script.js` to improve performance.

---

**Last Updated:** October 4, 2025