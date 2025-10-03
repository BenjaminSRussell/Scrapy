"""
FastAPI Metrics Server for UConn Scraping Pipeline Visualizer

This server provides real-time communication between the scraper and the frontend visualizer.
It receives events from the pipeline via POST requests and broadcasts them to connected
WebSocket clients for live visualization.
"""

import asyncio
import logging
from collections import deque
from datetime import datetime
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI(title="UConn Scraper Visualizer API", version="1.0.0")

# Enable CORS for frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins in development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.event_history: deque = deque(maxlen=1000)  # Keep last 1000 events

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"Client connected. Total connections: {len(self.active_connections)}")

        # Send event history to newly connected client
        for event in self.event_history:
            await websocket.send_json(event)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        logger.info(f"Client disconnected. Total connections: {len(self.active_connections)}")

    async def broadcast(self, message: dict[str, Any]):
        # Store in history
        self.event_history.append(message)

        # Broadcast to all connected clients
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error sending message to client: {e}")
                disconnected.append(connection)

        # Remove disconnected clients
        for conn in disconnected:
            self.disconnect(conn)


manager = ConnectionManager()


# Pydantic models for events
class PipelineEvent(BaseModel):
    event: str
    timestamp: str | None = None
    data: dict[str, Any] = {}

    class Config:
        extra = "allow"  # Allow additional fields


class URLDiscoveredEvent(PipelineEvent):
    event: str = "url_discovered"
    url: str
    source_url: str | None = None
    depth: int = 0


class URLValidatedEvent(PipelineEvent):
    event: str = "url_validated"
    url: str
    status_code: int
    success: bool
    error: str | None = None


class PageEnrichedEvent(PipelineEvent):
    event: str = "page_enriched"
    url: str
    entities_count: int = 0
    keywords_count: int = 0
    categories_count: int = 0


# Global statistics
class Stats:
    def __init__(self):
        self.urls_discovered = 0
        self.urls_validated = 0
        self.urls_failed = 0
        self.pages_enriched = 0
        self.status_codes: dict[int, int] = {}
        self.start_time = datetime.now()

    def to_dict(self):
        uptime = (datetime.now() - self.start_time).total_seconds()
        return {
            "urls_discovered": self.urls_discovered,
            "urls_validated": self.urls_validated,
            "urls_failed": self.urls_failed,
            "pages_enriched": self.pages_enriched,
            "status_codes": self.status_codes,
            "uptime_seconds": uptime,
        }


stats = Stats()


# API Endpoints
@app.get("/")
async def root():
    """Serve the visualizer frontend"""
    return FileResponse("visualizer/static/index.html")


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "active_connections": len(manager.active_connections),
        "stats": stats.to_dict(),
    }


@app.get("/stats")
async def get_stats():
    """Get current pipeline statistics"""
    return stats.to_dict()


@app.post("/event")
async def receive_event(event: dict[str, Any]):
    """
    Receive pipeline events from the scraper and broadcast to WebSocket clients.

    Supported events:
    - url_discovered: New URL discovered
    - url_validated: URL validation completed
    - page_enriched: Page enrichment completed
    - pipeline_start: Pipeline started
    - pipeline_complete: Pipeline completed
    - pipeline_error: Pipeline error occurred
    """
    # Add timestamp if not present
    if "timestamp" not in event:
        event["timestamp"] = datetime.now().isoformat()

    # Update statistics
    event_type = event.get("event")

    if event_type == "url_discovered":
        stats.urls_discovered += 1
    elif event_type == "url_validated":
        stats.urls_validated += 1
        status_code = event.get("status_code", 0)
        stats.status_codes[status_code] = stats.status_codes.get(status_code, 0) + 1
        if not event.get("success", False):
            stats.urls_failed += 1
    elif event_type == "page_enriched":
        stats.pages_enriched += 1

    logger.info(f"Event received: {event_type}")

    # Broadcast to all WebSocket clients
    await manager.broadcast(event)

    return {"status": "ok", "event_type": event_type}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time event streaming to the visualizer frontend.
    """
    await manager.connect(websocket)

    try:
        # Keep connection alive and listen for client messages
        while True:
            data = await websocket.receive_text()
            # Echo back or handle client commands if needed
            logger.debug(f"Received from client: {data}")

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)


# Mount static files for the frontend
try:
    app.mount("/static", StaticFiles(directory="visualizer/static"), name="static")
except RuntimeError:
    # Directory doesn't exist yet, will be created with frontend files
    pass


if __name__ == "__main__":
    import uvicorn

    logger.info("Starting UConn Scraper Visualizer Server...")
    logger.info("Visit http://localhost:8080 to view the visualizer")

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
        log_level="info",
    )
