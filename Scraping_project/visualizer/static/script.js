// UConn Scraper Visualizer - Main JavaScript
// Real-time visualization of the scraping pipeline

// Configuration
const WS_URL = `ws://${window.location.host}/ws`;
const GRAPH_WIDTH = document.getElementById('network-graph').clientWidth;
const GRAPH_HEIGHT = document.getElementById('network-graph').clientHeight - 40;

// State
let ws = null;
let stats = {
    urlsDiscovered: 0,
    urlsValidated: 0,
    urlsFailed: 0,
    pagesEnriched: 0
};

let rateHistory = [];
let statusCodes = {};
let lastEventTime = Date.now();
let nodes = [];
let links = [];

// D3 Force Simulation
let simulation, svg, linkGroup, nodeGroup;

// Charts
let rateChart, statusChart;

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    initializeGraphs();
    initializeCharts();
    connectWebSocket();
    setupEventHandlers();
});

// WebSocket Connection
function connectWebSocket() {
    ws = new WebSocket(WS_URL);

    ws.onopen = () => {
        console.log('Connected to metrics server');
        updateConnectionStatus(true);
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleEvent(data);
    };

    ws.onclose = () => {
        console.log('Disconnected from metrics server');
        updateConnectionStatus(false);
        // Reconnect after 3 seconds
        setTimeout(connectWebSocket, 3000);
    };

    ws.onerror = (error) => {
        console.error('WebSocket error:', error);
    };
}

function updateConnectionStatus(connected) {
    const indicator = document.getElementById('status-indicator');
    const text = document.getElementById('status-text');

    if (connected) {
        indicator.classList.remove('disconnected');
        indicator.classList.add('connected');
        text.textContent = 'Connected';
    } else {
        indicator.classList.remove('connected');
        indicator.classList.add('disconnected');
        text.textContent = 'Disconnected';
    }
}

// Event Handlers
function handleEvent(event) {
    const eventType = event.event;

    switch (eventType) {
        case 'url_discovered':
            handleURLDiscovered(event);
            break;
        case 'url_validated':
            handleURLValidated(event);
            break;
        case 'page_enriched':
            handlePageEnriched(event);
            break;
        case 'pipeline_start':
        case 'pipeline_complete':
        case 'pipeline_error':
            handlePipelineEvent(event);
            break;
    }

    addToEventLog(event);
    updateProcessingRate();
}

function handleURLDiscovered(event) {
    stats.urlsDiscovered++;
    updateStats();

    // Add node to graph
    const node = {
        id: event.url,
        url: event.url,
        status: 'discovered',
        depth: event.depth || 0
    };
    nodes.push(node);

    // Add link if source exists
    if (event.source_url) {
        const sourceNode = nodes.find(n => n.id === event.source_url);
        if (sourceNode) {
            links.push({
                source: sourceNode,
                target: node
            });
        }
    }

    updateGraph();
}

function handleURLValidated(event) {
    stats.urlsValidated++;

    if (!event.success) {
        stats.urlsFailed++;
    }

    // Update status codes
    const statusCode = event.status_code || 0;
    statusCodes[statusCode] = (statusCodes[statusCode] || 0) + 1;

    // Update node in graph
    const node = nodes.find(n => n.id === event.url);
    if (node) {
        node.status = event.success ? 'validated' : 'failed';
        node.statusCode = statusCode;
        updateNode(node);
    }

    updateStats();
    updateStatusChart();
}

function handlePageEnriched(event) {
    stats.pagesEnriched++;

    // Update node in graph
    const node = nodes.find(n => n.id === event.url);
    if (node) {
        node.status = 'enriched';
        node.entitiesCount = event.entities_count;
        node.keywordsCount = event.keywords_count;
        node.categoriesCount = event.categories_count;
        updateNode(node);
    }

    updateStats();
}

function handlePipelineEvent(event) {
    console.log('Pipeline event:', event);
}

// Statistics Updates
function updateStats() {
    document.getElementById('urls-discovered').textContent = stats.urlsDiscovered;
    document.getElementById('urls-validated').textContent = stats.urlsValidated;
    document.getElementById('urls-failed').textContent = stats.urlsFailed;
    document.getElementById('pages-enriched').textContent = stats.pagesEnriched;
}

function updateProcessingRate() {
    const now = Date.now();
    const timeDiff = (now - lastEventTime) / 1000; // seconds
    const rate = timeDiff > 0 ? 1 / timeDiff : 0;

    lastEventTime = now;

    // Add to history (keep last 60 data points)
    rateHistory.push({ time: now, rate: Math.min(rate, 10) });
    if (rateHistory.length > 60) {
        rateHistory.shift();
    }

    // Update display
    const avgRate = rateHistory.reduce((sum, item) => sum + item.rate, 0) / rateHistory.length;
    document.getElementById('processing-rate').innerHTML =
        `${avgRate.toFixed(1)} <span class="unit">pages/sec</span>`;

    // Update chart
    updateRateChart();
}

// D3 Graph Initialization
function initializeGraphs() {
    svg = d3.select('#network-graph')
        .append('svg')
        .attr('width', GRAPH_WIDTH)
        .attr('height', GRAPH_HEIGHT);

    // Add zoom behavior
    const zoom = d3.zoom()
        .scaleExtent([0.1, 4])
        .on('zoom', (event) => {
            svg.selectAll('g').attr('transform', event.transform);
        });

    svg.call(zoom);

    linkGroup = svg.append('g').attr('class', 'links');
    nodeGroup = svg.append('g').attr('class', 'nodes');

    simulation = d3.forceSimulation()
        .force('link', d3.forceLink().id(d => d.id).distance(100))
        .force('charge', d3.forceManyBody().strength(-300))
        .force('center', d3.forceCenter(GRAPH_WIDTH / 2, GRAPH_HEIGHT / 2))
        .force('collision', d3.forceCollide().radius(30));
}

function updateGraph() {
    // Update links
    const link = linkGroup.selectAll('line')
        .data(links, d => `${d.source.id}-${d.target.id}`);

    link.enter()
        .append('line')
        .attr('class', 'link');

    link.exit().remove();

    // Update nodes
    const node = nodeGroup.selectAll('g')
        .data(nodes, d => d.id);

    const nodeEnter = node.enter()
        .append('g')
        .attr('class', d => `node ${d.status}`)
        .call(d3.drag()
            .on('start', dragstarted)
            .on('drag', dragged)
            .on('end', dragended));

    nodeEnter.append('circle')
        .attr('r', 8)
        .attr('class', d => d.status);

    nodeEnter.append('title')
        .text(d => d.url);

    node.exit().remove();

    // Update simulation
    simulation.nodes(nodes);
    simulation.force('link').links(links);
    simulation.alpha(1).restart();

    simulation.on('tick', () => {
        linkGroup.selectAll('line')
            .attr('x1', d => d.source.x)
            .attr('y1', d => d.source.y)
            .attr('x2', d => d.target.x)
            .attr('y2', d => d.target.y);

        nodeGroup.selectAll('g')
            .attr('transform', d => `translate(${d.x},${d.y})`);
    });
}

function updateNode(node) {
    const nodeElement = nodeGroup.selectAll('g')
        .filter(d => d.id === node.id);

    nodeElement.attr('class', `node ${node.status} pulse`);

    nodeElement.select('circle')
        .transition()
        .duration(500)
        .attr('class', node.status);

    // Remove pulse class after animation
    setTimeout(() => {
        nodeElement.attr('class', `node ${node.status}`);
    }, 3000);
}

// Drag functions
function dragstarted(event, d) {
    if (!event.active) simulation.alphaTarget(0.3).restart();
    d.fx = d.x;
    d.fy = d.y;
}

function dragged(event, d) {
    d.fx = event.x;
    d.fy = event.y;
}

function dragended(event, d) {
    if (!event.active) simulation.alphaTarget(0);
    d.fx = null;
    d.fy = null;
}

// Chart Initialization
function initializeCharts() {
    // Processing Rate Chart
    const rateCtx = document.getElementById('rate-chart').getContext('2d');
    rateChart = new Chart(rateCtx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'Pages/sec',
                data: [],
                borderColor: '#10b981',
                backgroundColor: 'rgba(16, 185, 129, 0.1)',
                tension: 0.4,
                fill: true
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: {
                    beginAtZero: true,
                    grid: { color: '#334155' },
                    ticks: { color: '#94a3b8' }
                },
                x: {
                    grid: { color: '#334155' },
                    ticks: { color: '#94a3b8', display: false }
                }
            },
            plugins: {
                legend: { display: false }
            }
        }
    });

    // Status Code Chart
    const statusCtx = document.getElementById('status-chart').getContext('2d');
    statusChart = new Chart(statusCtx, {
        type: 'doughnut',
        data: {
            labels: [],
            datasets: [{
                data: [],
                backgroundColor: [
                    '#10b981', // 200s - green
                    '#f59e0b', // 300s - yellow
                    '#ef4444', // 400s - red
                    '#8b5cf6', // 500s - purple
                    '#64748b'  // others - gray
                ]
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: { color: '#94a3b8' }
                }
            }
        }
    });
}

function updateRateChart() {
    if (rateHistory.length === 0) return;

    rateChart.data.labels = rateHistory.map((_, i) => i);
    rateChart.data.datasets[0].data = rateHistory.map(item => item.rate);
    rateChart.update('none'); // No animation for smooth updates
}

function updateStatusChart() {
    const labels = Object.keys(statusCodes).sort();
    const data = labels.map(code => statusCodes[code]);

    statusChart.data.labels = labels.map(code => `${code}: ${statusCodes[code]}`);
    statusChart.data.datasets[0].data = data;
    statusChart.update();
}

// Event Log
function addToEventLog(event) {
    const log = document.getElementById('event-log');
    const eventDiv = document.createElement('div');
    eventDiv.className = `event-item ${event.event.replace('_', '-')}`;

    const time = new Date(event.timestamp).toLocaleTimeString();
    let details = '';

    switch (event.event) {
        case 'url_discovered':
            details = `URL: ${truncateURL(event.url)}`;
            break;
        case 'url_validated':
            details = `${event.status_code} - ${truncateURL(event.url)}`;
            break;
        case 'page_enriched':
            details = `${truncateURL(event.url)} (${event.entities_count} entities, ${event.keywords_count} keywords)`;
            break;
        default:
            details = JSON.stringify(event.data || {});
    }

    eventDiv.innerHTML = `
        <div class="event-time">${time}</div>
        <div class="event-type">${formatEventType(event.event)}</div>
        <div class="event-details">${details}</div>
    `;

    log.insertBefore(eventDiv, log.firstChild);

    // Keep only last 100 events
    while (log.children.length > 100) {
        log.removeChild(log.lastChild);
    }
}

function truncateURL(url) {
    if (url.length > 50) {
        return url.substring(0, 47) + '...';
    }
    return url;
}

function formatEventType(eventType) {
    return eventType.split('_').map(word =>
        word.charAt(0).toUpperCase() + word.slice(1)
    ).join(' ');
}

// Setup Event Handlers
function setupEventHandlers() {
    document.getElementById('clear-log').addEventListener('click', () => {
        document.getElementById('event-log').innerHTML = '';
    });
}
