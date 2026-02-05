// ===================================================================
// Global Variables & State Management
// ===================================================================
let tradesData = [];
let form8kData = [];
let filteredTrades = [];
let filteredForm8k = [];
let charts = {};
let customWatchlistTickers = []; // Store custom watchlist
let form8kSortOrder = 'newest'; // Default sort order for Form 8-K

// Default watchlist (all 77 stocks)
const DEFAULT_WATCHLIST = [
    "MSFT", "NVDA", "TSLA", "GOOGL", "META", "AMZN", "NFLX", "AMD",
    "INTC", "COIN", "LYFT", "ORCL", "AVGO", "ADBE", "PYPL", "PLTR",
    "SMCI", "SOFI", "SMR", "GME", "HIMS", "CRWV", "XPEV", "HOOD",
    "OKLO", "ACHR", "IREN", "NBIS", "MU", "SNOW", "APP", "TSM",
    "ASTS", "MRVL", "BA", "PDD", "SOUN", "PANW", "TEM", "LLY",
    "ALGN", "SPOT", "CVNA", "SHOP", "DUOL", "NKE", "CSCO", "BULL",
    "JNJ", "LCID", "KO", "GE", "BE", "NEE", "PEP", "RR", "IONQ",
    "QCOM", "LNTH", "CFLT", "LMND", "JOBY", "CAT", "OPEN", "RIVN",
    "PFE", "CNC", "NVO", "NOW", "CVS", "ABT", "IBM", "JPM", "NVAX", "BRK-B", "UNH", "AAPL"
];

// ===================================================================
// Data Loading & Update Functions
// ===================================================================
async function loadData() {
    try {
        const tradesResponse = await fetch('data/trades_data.json');
        const tradesJson = await tradesResponse.json();
        tradesData = tradesJson.trades || [];
        
        const form8kResponse = await fetch('data/form8k_data.json');
        const form8kJson = await form8kResponse.json();
        form8kData = form8kJson.filings || [];
        
        // Debug: Log the first filing to see its structure
        if (form8kData.length > 0) {
            console.log('Sample Form 8-K filing:', form8kData[0]);
            console.log('Items field:', form8kData[0].items);
            console.log('Items type:', typeof form8kData[0].items);
            console.log('Items is array:', Array.isArray(form8kData[0].items));
        }
        
        updateStats(tradesJson.stats);
        applyFilters(); // This will apply both regular filters and watchlist filters
        renderForm8kTable();
        renderCharts();
        
        document.getElementById('lastUpdate').textContent = 
            `Last updated: ${new Date(tradesJson.lastUpdated || Date.now()).toLocaleString()}`;
    } catch (error) {
        console.error('Error loading data:', error);
        document.getElementById('form4Body').innerHTML = `
            <tr>
                <td colspan="9" class="empty-state">
                    <p>No data available. Waiting for first data collection...</p>
                </td>
            </tr>
        `;
    }
}

function updateStats(stats) {
    if (!stats) return;
    
    document.getElementById('totalAlerts').textContent = stats.totalAlerts.toLocaleString();
    document.getElementById('totalBuys').textContent = stats.buys.toLocaleString();
    document.getElementById('totalSells').textContent = stats.sells.toLocaleString();
    
    // Format total value with K, M, or B
    const totalValue = stats.totalValue;
    let formattedValue;
    if (totalValue >= 1000000000) {
        formattedValue = '$' + (totalValue / 1000000000).toFixed(2) + 'B';
    } else if (totalValue >= 1000000) {
        formattedValue = '$' + (totalValue / 1000000).toFixed(2) + 'M';
    } else if (totalValue >= 1000) {
        formattedValue = '$' + (totalValue / 1000).toFixed(2) + 'K';
    } else {
        formattedValue = '$' + totalValue.toFixed(2);
    }
    document.getElementById('totalValue').textContent = formattedValue;
    
    document.getElementById('total8K').textContent = form8kData.length.toLocaleString();
    
    const uniqueTickers = new Set([...tradesData.map(t => t.symbol), ...form8kData.map(f => f.symbol)]);
    document.getElementById('activeTickers').textContent = uniqueTickers.size.toLocaleString();
}

// ===================================================================
// Watchlist Management
// ===================================================================
function applyCustomWatchlist() {
    const input = document.getElementById('customWatchlist').value.trim();
    if (input) {
        // Parse comma-separated tickers
        customWatchlistTickers = input.split(',').map(t => t.trim().toUpperCase()).filter(t => t);
        console.log('Custom watchlist applied:', customWatchlistTickers);
    } else {
        customWatchlistTickers = [];
    }
    applyFilters();
}

function clearCustomWatchlist() {
    customWatchlistTickers = [];
    document.getElementById('customWatchlist').value = '';
    applyFilters();
}

// ===================================================================
// Form 4 Table Rendering
// ===================================================================
function renderForm4Table() {
    const tbody = document.getElementById('form4Body');
    
    if (filteredTrades.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="9" class="empty-state">
                    <p>No trades match your filters</p>
                </td>
            </tr>
        `;
        return;
    }
    
    tbody.innerHTML = filteredTrades.map(trade => `
        <tr>
            <td><span class="badge ${trade.type.toLowerCase()}">${trade.type}</span></td>
            <td><span class="ticker">${trade.symbol}</span></td>
            <td>
                <div>${trade.name}</div>
                <div class="transaction-code">Code ${trade.transactionCode || 'N/A'}: ${trade.transactionDescription || 'Unknown'}</div>
            </td>
            <td>${trade.shares.toLocaleString()}</td>
            <td>${trade.price > 0 ? '$' + trade.price.toFixed(2) : 'N/A'}</td>
            <td class="${trade.type === 'BUY' ? 'value-positive' : 'value-negative'}">
                ${trade.totalValue > 0 ? '$' + trade.totalValue.toLocaleString() : 'N/A'}
            </td>
            <td>${trade.transactionDate}</td>
            <td>${trade.filingDate}</td>
            <td>
                ${trade.secLink ? `<a href="${trade.secLink}" target="_blank" class="sec-link">View Form 4 →</a>` : 'N/A'}
            </td>
        </tr>
    `).join('');
}

// ===================================================================
// Form 8-K Table Rendering
// ===================================================================
function get8KExplanation(filing) {
    // Map of Form 8-K item codes to plain English explanations
    const itemExplanations = {
        '1.01': 'Company signed an important contract',
        '1.02': 'Major contract terminated or cancelled',
        '1.03': '🚨 Bankruptcy or receivership filing',
        '2.01': 'Acquired or sold significant business/assets',
        '2.02': 'Earnings release or financial results',
        '2.03': 'New debt issued (loans, bonds, credit)',
        '2.04': 'Debt acceleration or penalties triggered',
        '2.05': 'Restructuring, layoffs, or plant closures',
        '2.06': 'Large asset write-downs or impairments',
        '3.01': '🚨 Risk of delisting from exchange',
        '3.02': 'Private stock sale (unregistered)',
        '3.03': 'Changes to shareholder rights or dividends',
        '4.01': 'Auditor changed (fired or resigned)',
        '4.02': '🚩 Financial restatement likely - accounting issue',
        '5.01': 'Change in company ownership/control',
        '5.02': 'CEO/CFO or director change',
        '5.03': 'Corporate governance rules updated',
        '5.04': 'Employee stock trading temporarily suspended',
        '5.05': 'Code of ethics amended or waived',
        '7.01': 'Public disclosure of material information',
        '8.01': 'Other material event (lawsuit, investigation, etc.)',
        '9.01': 'Financial statements and exhibits attached'
    };
    
    // Debug logging
    console.log('Processing filing:', filing.symbol, 'Items:', filing.items);
    
    // Check if filing has item codes
    if (filing.items && filing.items.length > 0) {
        const items = Array.isArray(filing.items) ? filing.items : 
                      (typeof filing.items === 'string' ? filing.items.split(',').map(i => i.trim()) : []);
        
        console.log('Parsed items:', items);
        
        // Build descriptions for all items found
        const descriptions = [];
        for (let item of items) {
            const trimmedItem = item.trim();
            if (itemExplanations[trimmedItem]) {
                descriptions.push(`<span class="item-code">Item ${trimmedItem}</span><span class="item-description">${itemExplanations[trimmedItem]}</span>`);
            } else {
                console.log('No explanation for item:', trimmedItem);
            }
        }
        
        // If we found descriptions, return them joined
        if (descriptions.length > 0) {
            console.log('Returning descriptions:', descriptions);
            return '<div style="line-height: 1.8;">' + descriptions.join('<br>') + '</div>';
        }
    }
    
    console.log('Returning default explanation for', filing.symbol);
    // Default explanation if no specific item code
    return "Material company event requiring SEC disclosure";
}

function applyForm8KSort() {
    form8kSortOrder = document.getElementById('sort8K').value;
    renderForm8kTable();
}

function renderForm8kTable() {
    const tbody = document.getElementById('form8kBody');
    
    // Apply watchlist filter to Form 8-K data
    let displayForm8k = [...form8kData];
    if (customWatchlistTickers.length > 0) {
        displayForm8k = displayForm8k.filter(filing => 
            customWatchlistTickers.includes(filing.symbol)
        );
    }
    
    // Sort by filed date
    displayForm8k.sort((a, b) => {
        const dateA = new Date(a.filedDate);
        const dateB = new Date(b.filedDate);
        if (form8kSortOrder === 'newest') {
            return dateB - dateA; // Newest first
        } else {
            return dateA - dateB; // Oldest first
        }
    });
    
    if (displayForm8k.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="6" class="empty-state">
                    <p>No Form 8-K filings available</p>
                </td>
            </tr>
        `;
        return;
    }
    
    tbody.innerHTML = displayForm8k.map(filing => {
        // Format report date (event date)
        const reportDate = filing.reportDate !== 'Unknown' ? 
            new Date(filing.reportDate).toLocaleDateString('en-US', { 
                year: 'numeric', 
                month: 'short', 
                day: 'numeric' 
            }) : 'Unknown';
        
        // Format filed date nicely
        const filedDate = filing.filedDate !== 'Unknown' ? 
            new Date(filing.filedDate).toLocaleDateString('en-US', { 
                year: 'numeric', 
                month: 'short', 
                day: 'numeric' 
            }) : 'Unknown';
        
        // Format accepted time
        const acceptedTime = filing.acceptedDate !== 'Unknown' ?
            new Date(filing.acceptedDate).toLocaleTimeString('en-US', {
                hour: 'numeric',
                minute: '2-digit',
                hour12: true
            }) : 'Unknown';
        
        return `
            <tr>
                <td><span class="ticker">${filing.symbol}</span></td>
                <td style="color: var(--accent-yellow); font-weight: 600;">${reportDate}</td>
                <td>${filedDate}</td>
                <td>${acceptedTime}</td>
                <td style="font-size: 0.9rem;">
                    ${get8KExplanation(filing)}
                </td>
                <td>
                    ${filing.reportUrl ? `<a href="${filing.reportUrl}" target="_blank" class="sec-link">View 8-K →</a>` : 'N/A'}
                </td>
            </tr>
        `;
    }).join('');
}

// ===================================================================
// Filter Management
// ===================================================================
function applyFilters() {
    const type = document.getElementById('filterType').value;
    const ticker = document.getElementById('filterTicker').value.toUpperCase();
    const minValue = parseFloat(document.getElementById('filterMinValue').value) || 0;
    const sortBy = document.getElementById('sortBy').value;
    
    filteredTrades = tradesData.filter(trade => {
        // Apply type filter
        if (type !== 'all' && trade.type !== type) return false;
        
        // Apply single ticker search filter
        if (ticker && !trade.symbol.includes(ticker)) return false;
        
        // Apply custom watchlist filter (if set)
        if (customWatchlistTickers.length > 0 && !customWatchlistTickers.includes(trade.symbol)) return false;
        
        // Apply minimum value filter
        if (trade.totalValue < minValue) return false;
        
        return true;
    });
    
    // Sort filtered trades
    filteredTrades.sort((a, b) => {
        if (sortBy === 'value') return b.totalValue - a.totalValue;
        if (sortBy === 'shares') return b.shares - a.shares;
        return new Date(b.filingDate) - new Date(a.filingDate);
    });
    
    renderForm4Table();
    renderForm8kTable(); // Re-render 8-K table with watchlist filter
}

function resetFilters() {
    document.getElementById('filterType').value = 'all';
    document.getElementById('filterTicker').value = '';
    document.getElementById('filterMinValue').value = '100000';
    document.getElementById('sortBy').value = 'date';
    applyFilters();
}

// ===================================================================
// Tab Switching
// ===================================================================
function switchTab(tab) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    event.target.classList.add('active');
    
    document.getElementById('form4Content').style.display = tab === 'form4' ? 'block' : 'none';
    document.getElementById('form8kContent').style.display = tab === 'form8k' ? 'block' : 'none';
    document.getElementById('analyticsContent').style.display = tab === 'analytics' ? 'block' : 'none';
    
    // Show/hide Form 8-K controls
    document.getElementById('form8kControls').style.display = tab === 'form8k' ? 'flex' : 'none';
}

// ===================================================================
// Charts Rendering
// ===================================================================
function renderCharts() {
    const buys = tradesData.filter(t => t.type === 'BUY').length;
    const sells = tradesData.filter(t => t.type === 'SELL').length;
    
    // Buy vs Sell Chart
    const buySellCtx = document.getElementById('buySellChart').getContext('2d');
    charts.buySell = new Chart(buySellCtx, {
        type: 'doughnut',
        data: {
            labels: ['Buys', 'Sells'],
            datasets: [{
                data: [buys, sells],
                backgroundColor: ['#00ff88', '#ff3366'],
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: {
                    labels: { color: '#e8eaed', font: { family: 'Space Mono' } }
                }
            }
        }
    });
    
    // Top Tickers Chart
    const tickerCounts = {};
    tradesData.forEach(t => {
        tickerCounts[t.symbol] = (tickerCounts[t.symbol] || 0) + 1;
    });
    const topTickers = Object.entries(tickerCounts)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 10);
    
    const tickerCtx = document.getElementById('tickerChart').getContext('2d');
    charts.ticker = new Chart(tickerCtx, {
        type: 'bar',
        data: {
            labels: topTickers.map(t => t[0]),
            datasets: [{
                label: 'Trades',
                data: topTickers.map(t => t[1]),
                backgroundColor: '#00d4ff',
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            indexAxis: 'y',
            scales: {
                x: { ticks: { color: '#9ca3af' }, grid: { color: '#2a3140' } },
                y: { ticks: { color: '#9ca3af', font: { family: 'Space Mono' } }, grid: { display: false } }
            },
            plugins: {
                legend: { display: false }
            }
        }
    });
    
    // Value Distribution Chart
    const valueBuckets = {
        '<$50K': 0, '$50K-$100K': 0, '$100K-$500K': 0, 
        '$500K-$1M': 0, '$1M-$5M': 0, '>$5M': 0
    };
    tradesData.forEach(t => {
        const val = t.totalValue;
        if (val < 50000) valueBuckets['<$50K']++;
        else if (val < 100000) valueBuckets['$50K-$100K']++;
        else if (val < 500000) valueBuckets['$100K-$500K']++;
        else if (val < 1000000) valueBuckets['$500K-$1M']++;
        else if (val < 5000000) valueBuckets['$1M-$5M']++;
        else valueBuckets['>$5M']++;
    });
    
    const valueCtx = document.getElementById('valueChart').getContext('2d');
    charts.value = new Chart(valueCtx, {
        type: 'bar',
        data: {
            labels: Object.keys(valueBuckets),
            datasets: [{
                label: 'Trades',
                data: Object.values(valueBuckets),
                backgroundColor: '#ffd60a',
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            scales: {
                x: { ticks: { color: '#9ca3af' }, grid: { display: false } },
                y: { ticks: { color: '#9ca3af' }, grid: { color: '#2a3140' } }
            },
            plugins: {
                legend: { display: false }
            }
        }
    });
    
    // Timeline Chart
    const last30Days = Array.from({length: 30}, (_, i) => {
        const d = new Date();
        d.setDate(d.getDate() - (29 - i));
        return d.toISOString().split('T')[0];
    });
    
    const dailyCounts = {};
    last30Days.forEach(date => dailyCounts[date] = 0);
    tradesData.forEach(t => {
        const date = t.filingDate;
        if (dailyCounts[date] !== undefined) {
            dailyCounts[date]++;
        }
    });
    
    const timelineCtx = document.getElementById('timelineChart').getContext('2d');
    charts.timeline = new Chart(timelineCtx, {
        type: 'line',
        data: {
            labels: last30Days.map(d => new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })),
            datasets: [{
                label: 'Filings',
                data: Object.values(dailyCounts),
                borderColor: '#00ff88',
                backgroundColor: 'rgba(0, 255, 136, 0.1)',
                fill: true,
                tension: 0.4,
                borderWidth: 2
            }]
        },
        options: {
            responsive: true,
            scales: {
                x: { ticks: { color: '#9ca3af' }, grid: { color: '#2a3140' } },
                y: { ticks: { color: '#9ca3af' }, grid: { color: '#2a3140' } }
            },
            plugins: {
                legend: { display: false }
            }
        }
    });
}

// ===================================================================
// Initialization & Auto-Refresh
// ===================================================================
// Refresh data every 5 minutes
setInterval(loadData, 300000);

// Load data on page load
loadData();
