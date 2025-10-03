# UConn Scraping Pipeline - Real-Time Visualizer

A stunning real-time visualization dashboard for the UConn web scraping pipeline. Watch your scraper come alive with live network graphs, statistics, and event streams!

## Features

### 🕸️ Live Network Graph
- **Force-directed D3.js visualization** showing URL discovery in real-time
- Watch URLs being discovered, validated, and enriched
- Color-coded nodes:
  - Grey: Discovered
  - Green: Validated successfully
  - Red: Failed validation
  - Purple (glowing): Enriched with data
- Interactive: drag nodes, zoom, pan
- Smooth animations as the web grows

### 📊 Live Statistics Dashboard
- **URLs Discovered**: Total URLs found
- **URLs Validated**: Successfully validated URLs
- **URLs Failed**: Failed validations
- **Pages Enriched**: Pages with extracted data
- **Processing Rate**: Real-time pages/second metric

### 📈 Real-Time Charts
- **Processing Rate Chart**: Line chart showing throughput over last 60 seconds
- **HTTP Status Codes**: Donut chart showing distribution of status codes

### 📝 Event Log
- Real-time stream of all pipeline events
- Color-coded by event type
- Shows timestamps and details
- Auto-scrolling, keeps last 100 events

## Quick Start

### 1. Install Dependencies

```bash
pip install fastapi uvicorn websockets
```

### 2. Start the Visualizer Server

```bash
cd Scraping_project/visualizer
python server.py
```

The server will start on http://localhost:8080

### 3. Open Your Browser

Visit http://localhost:8080 to see the visualizer.

### 4. Run Your Scraper

The scraper will automatically send events to the visualizer:

```bash
cd Scraping_project
scrapy crawl discovery_spider
```

## How It Works

### Architecture

```
┌─────────────┐    HTTP POST     ┌──────────────┐    WebSocket    ┌─────────────┐
│   Scraper   │ ───────────────> │   FastAPI    │ ──────────────> │  Frontend   │
│  (Python)   │    /event        │    Server    │      /ws        │  (Browser)  │
└─────────────┘                  └──────────────┘                 └─────────────┘
```

### Event Flow

1. **Scraper emits event**: When a URL is discovered/validated/enriched
2. **POST to `/event`**: Metrics emitter sends JSON event to server
3. **Broadcast via WebSocket**: Server broadcasts to all connected clients
4. **Update visualization**: Frontend updates graphs, stats, and logs in real-time

### Event Types

```javascript
// URL Discovered
{
  "event": "url_discovered",
  "url": "https://uconn.edu/admissions",
  "source_url": "https://uconn.edu",
  "depth": 1,
  "timestamp": "2025-01-03T14:30:45"
}

// URL Validated
{
  "event": "url_validated",
  "url": "https://uconn.edu/admissions",
  "status_code": 200,
  "success": true,
  "timestamp": "2025-01-03T14:30:46"
}

// Page Enriched
{
  "event": "page_enriched",
  "url": "https://uconn.edu/admissions",
  "entities_count": 15,
  "keywords_count": 10,
  "categories_count": 3,
  "timestamp": "2025-01-03T14:30:47"
}
```

## Integration with Pipeline

### Automatic Integration

The metrics emitter is automatically enabled when the visualizer server is running. No code changes needed!

### Manual Integration

To emit custom events from your code:

```python
from src.common.metrics_emitter import send_event

# Send custom event
send_event("custom_event", {
    "message": "Something important happened",
    "count": 42
})
```

### Configuration

Edit `src/common/constants.py`:

```python
# Metrics configuration
METRICS_SERVER_HOST = "localhost"
METRICS_SERVER_PORT = 8080
METRICS_ENABLED = True  # Set to False to disable
```

## API Endpoints

### WebSocket: `/ws`
Real-time event stream for the frontend.

### POST `/event`
Receive events from the scraper.

**Request Body:**
```json
{
  "event": "url_discovered",
  "url": "https://example.com",
  "...": "additional data"
}
```

### GET `/health`
Health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "active_connections": 1,
  "stats": {
    "urls_discovered": 150,
    "urls_validated": 120,
    "urls_failed": 5,
    "pages_enriched": 100,
    "uptime_seconds": 3600
  }
}
```

### GET `/stats`
Get current statistics.

## Customization

### Styling

Edit `visualizer/static/style.css`:

```css
:root {
    --primary-color: #2563eb;    /* Change colors */
    --success-color: #10b981;
    --error-color: #ef4444;
}
```

### Graph Layout

Edit `visualizer/static/script.js`:

```javascript
simulation = d3.forceSimulation()
    .force('link', d3.forceLink().distance(150))  // Adjust distance
    .force('charge', d3.forceManyBody().strength(-500))  // Adjust repulsion
    .force('center', d3.forceCenter(width / 2, height / 2))
```

## Troubleshooting

### "Connection Failed"

1. Ensure the visualizer server is running:
   ```bash
   python visualizer/server.py
   ```

2. Check that port 8080 is not blocked by firewall

3. Verify the scraper can reach the server:
   ```bash
   curl http://localhost:8080/health
   ```

### No Events Showing

1. Check that metrics are enabled in `constants.py`:
   ```python
   METRICS_ENABLED = True
   ```

2. Verify the scraper is running

3. Check server logs for errors:
   ```bash
   python visualizer/server.py
   # Look for "Event received: ..." messages
   ```

### Graph Performance Issues

For large crawls (1000+ nodes), performance may degrade. Solutions:

1. **Limit displayed nodes**: Modify `script.js` to show only recent nodes
2. **Increase force distance**: Makes graph less dense
3. **Disable animations**: Set `simulation.stop()` after initial layout

## Production Deployment

### Using Gunicorn

```bash
pip install gunicorn
gunicorn visualizer.server:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8080
```

### Using Docker

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY visualizer/ /app/visualizer/
COPY src/common/constants.py /app/src/common/

RUN pip install fastapi uvicorn websockets

EXPOSE 8080
CMD ["uvicorn", "visualizer.server:app", "--host", "0.0.0.0", "--port", "8080"]
```

### With Nginx Reverse Proxy

```nginx
server {
    listen 80;
    server_name visualizer.example.com;

    location / {
        proxy_pass http://localhost:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
```

## Future Enhancements

Planned features:

- [ ] **Historical playback**: Replay past crawls
- [ ] **Export functionality**: Save graphs as images
- [ ] **Advanced filtering**: Filter nodes by status/depth
- [ ] **Multi-pipeline support**: Visualize multiple crawls simultaneously
- [ ] **Performance metrics**: Memory, CPU usage graphs
- [ ] **Alert system**: Pop-up notifications for errors

## License

Part of the UConn Web Scraping Pipeline project.
