// Gurgaon Rental Explorer - Production Dashboard Engine
// Real HUDA Sector Multi-Vertex Polygons (OpenStreetMap) • Zero Overlap • Zero-Lag Client Reactive Filtering

let MAP;
let SECTOR_GEOJSON_DATA = null;
let TRANSIT_GEOJSON_DATA = null;
let ALL_LISTINGS = [];
let GEOJSON_LAYER = null;
let METRO_LAYER_GROUP = null;
let MARKER_LAYER_GROUP = null;
let CURRENT_SECTOR_POPUP = null;
let popupCloseTimer = null;
let isCursorOverPopup = false;

// Reactive State
let activeBHK = 'all';
let activeFurnishing = 'all';
let activeType = 'all';
let activePortal = 'all';
let searchQuery = '';
let showPins = false;
let selectedSectorName = null;

// Initialize Application
async function initApp() {
  try {
    let payload;
    if (window.__EMBEDDED_DATA__) {
      payload = window.__EMBEDDED_DATA__;
    } else {
      const resp = await fetch('./data.json?v=' + Date.now());
      if (!resp.ok) throw new Error(`HTTP ${resp.status} loading data.json`);
      payload = await resp.json();
    }

    SECTOR_GEOJSON_DATA = payload.sectors;
    ALL_LISTINGS = payload.listings;
    TRANSIT_GEOJSON_DATA = payload.transit;

    initLeafletMap();
    applyFilters();
  } catch (err) {
    console.error('Initialization error:', err);
    document.getElementById('activeCountBadge').innerText = 'Data Error';
    document.getElementById('drawerBody').innerHTML = `
      <div class="p-4 bg-red-900/30 border border-red-800 rounded-xl text-red-300 text-xs">
        <strong>Error loading dataset:</strong> ${err.message}<br/>
        Please ensure <code>data.json</code> is present in the web directory.
      </div>
    `;
  }
}

// Initialize Leaflet Map
function initLeafletMap() {
  MAP = L.map('map', {
    center: [28.468, 77.085], // Centered at DLF Phase 4 / Sector 43 / Golf Course Rd
    zoom: 13,
    zoomControl: true
  });

  // Dedicated Labels Pane with high zIndex (650)
  // Ensures natural map landmarks, street names, and POIs float gracefully ABOVE choropleth polygons (zIndex 400)
  MAP.createPane('labelsPane');
  MAP.getPane('labelsPane').style.zIndex = 650;
  MAP.getPane('labelsPane').style.pointerEvents = 'none';

  // 1. OpenStreetMap (Standard - 100% Free, Keyless, Dynamic Zoom Landmarks, Zero Watermark)
  const osmTiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19
  });
  osmTiles.addTo(MAP); // Default active basemap

  // 2. Esri World Streets (Rich Commercial Hubs, Malls, Highways & Roads)
  const esriStreets = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}', {
    attribution: 'Tiles &copy; Esri &mdash; Source: Esri, DeLorme, NAVTEQ, USGS, Intermap, iPC, NRCAN, Esri Japan, METI, Esri China, TomTom',
    maxZoom: 19
  });

  // 3. Esri World Topo Map (Topography, Landmarks & Points of Interest)
  const esriTopo = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}', {
    attribution: 'Tiles &copy; Esri, DeLorme, NAVTEQ, TomTom, Intermap, iPC, USGS, FAO, NPS, NRCAN, GeoBase, Kadaster NL, Ordnance Survey, Esri Japan, METI, Esri China, and the GIS User Community',
    maxZoom: 19
  });

  // 4. Esri Satellite with Reference Labels
  const esriSatellite = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    attribution: 'Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community',
    maxZoom: 19
  });

  const esriLabels = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}', {
    attribution: 'Labels &copy; Esri',
    maxZoom: 19,
    pane: 'labelsPane'
  });

  const satelliteMap = L.layerGroup([esriSatellite, esriLabels]);

  MARKER_LAYER_GROUP = L.layerGroup();

  const baseMaps = {
    "OpenStreetMap (Standard)": osmTiles,
    "Esri World Streets": esriStreets,
    "Esri Topo & Landmarks": esriTopo,
    "Esri Satellite Imagery": satelliteMap
  };

  const overlayMaps = {
    "🏢 Property Unit Pins": MARKER_LAYER_GROUP
  };

  L.control.layers(baseMaps, overlayMaps, { position: 'topright' }).addTo(MAP);

  // Background map click resets selection if clicked outside polygons
  MAP.on('click', function(e) {
    if (e.originalEvent && e.originalEvent._sectorClicked) return;
    if (selectedSectorName) {
      clearSelectedSector();
    } else if (CURRENT_SECTOR_POPUP) {
      MAP.closePopup(CURRENT_SECTOR_POPUP);
      CURRENT_SECTOR_POPUP = null;
    }
  });

  MAP.on('popupclose', function(e) {
    if (e.popup === CURRENT_SECTOR_POPUP) {
      CURRENT_SECTOR_POPUP = null;
      isCursorOverPopup = false;
    }
  });

  // Render Official Multi-Vertex Polygons
  renderSectorPolygons();
}

// Render Transit Overlays (Metro Tracks & Stations)
function renderTransitOverlays() {
  if (!TRANSIT_GEOJSON_DATA || !TRANSIT_GEOJSON_DATA.features) return;
  METRO_LAYER_GROUP.clearLayers();

  TRANSIT_GEOJSON_DATA.features.forEach(feat => {
    const p = feat.properties;
    const geom = feat.geometry;

    if (p.type === 'metro_line') {
      const coords = geom.coordinates.map(pt => [pt[1], pt[0]]);
      // Shadow / Outline casing
      L.polyline(coords, {
        color: '#090d16',
        weight: 6.5,
        opacity: 0.75,
        lineCap: 'round',
        lineJoin: 'round'
      }).addTo(METRO_LAYER_GROUP);

      // Main line track
      const line = L.polyline(coords, {
        color: p.line_color || '#eab308',
        weight: 4,
        opacity: 0.95,
        lineCap: 'round',
        lineJoin: 'round'
      }).addTo(METRO_LAYER_GROUP);

      line.bindTooltip(`🚇 <strong>${p.name}</strong> (${p.network})`, {
        sticky: true,
        className: 'transit-tooltip'
      });
    } else if (p.type === 'metro_station') {
      const lat = geom.coordinates[1];
      const lon = geom.coordinates[0];
      const isInterchange = !!p.interchange;
      const stColor = p.color || (isInterchange ? '#f59e0b' : '#eab308');

      const marker = L.circleMarker([lat, lon], {
        radius: isInterchange ? 6.5 : 5,
        fillColor: stColor,
        color: '#ffffff',
        weight: 2,
        opacity: 1,
        fillOpacity: 1
      }).addTo(METRO_LAYER_GROUP);

      marker.bindTooltip(`🚇 <strong>${p.name}</strong>${isInterchange ? ' <em>(Interchange)</em>' : ''}`, {
        direction: 'top',
        offset: [0, -6],
        className: 'transit-tooltip'
      });

      marker.bindPopup(`
        <div class="p-2 min-w-[190px]">
          <div class="font-bold text-white text-xs flex items-center gap-1.5 border-b border-slate-700/80 pb-1 mb-1">
            <span>🚇</span> <span>${p.name}</span>
          </div>
          <div class="text-[11px] text-amber-300 font-semibold">${p.line || p.network}</div>
          ${isInterchange ? '<div class="text-[10px] text-emerald-400 font-bold mt-0.5">🔄 Major Transit Interchange</div>' : ''}
          <div class="text-[10px] text-slate-400 mt-1">Gurgaon Metro Network Station</div>
        </div>
      `);
    }
  });
}

// Render Sector Polygons from GeoJSON
function renderSectorPolygons() {
  if (GEOJSON_LAYER) {
    MAP.removeLayer(GEOJSON_LAYER);
  }

  GEOJSON_LAYER = L.geoJSON(SECTOR_GEOJSON_DATA, {
    style: function(feature) {
      return {
        fillColor: '#3b82f6',
        fillOpacity: 0.45,
        color: '#2563eb',
        weight: 1.8,
        dashArray: null
      };
    },
    onEachFeature: function(feature, layer) {
      const name = feature.properties.sector_name;
      let hoverPauseTimer = null;
      let lastLatLng = null;

      // Hover-and-pause: Only trigger popup when user stops/pauses cursor over the sector
      layer.on('mouseover', function(e) {
        lastLatLng = e.latlng;
        if (selectedSectorName !== name) {
          layer.setStyle({ weight: 3, color: '#ffffff' });
          if (!L.Browser.ie && !L.Browser.opera && !L.Browser.edge) {
            layer.bringToFront();
          }
        }

        // If a popup is already showing from another sector, close it immediately!
        if (CURRENT_SECTOR_POPUP && CURRENT_SECTOR_POPUP._sectorName !== name) {
          if (popupCloseTimer) {
            clearTimeout(popupCloseTimer);
            popupCloseTimer = null;
          }
          MAP.closePopup(CURRENT_SECTOR_POPUP);
          CURRENT_SECTOR_POPUP = null;
          isCursorOverPopup = false;
        } else if (popupCloseTimer) {
          // If returning to the same sector before close timer expired, keep it
          clearTimeout(popupCloseTimer);
          popupCloseTimer = null;
        }

        // If popup is already showing for this sector, do not recreate
        if (CURRENT_SECTOR_POPUP && CURRENT_SECTOR_POPUP._sectorName === name) {
          return;
        }

        // Start dwell / pause timer (450ms intentional pause required)
        if (hoverPauseTimer) clearTimeout(hoverPauseTimer);
        hoverPauseTimer = setTimeout(function() {
          showSectorPopup(name, lastLatLng, true);
        }, 450);
      });

      layer.on('mousemove', function(e) {
        lastLatLng = e.latlng;
        // If popup already open for this sector, do not reset pause timer
        if (CURRENT_SECTOR_POPUP && CURRENT_SECTOR_POPUP._sectorName === name) {
          return;
        }
        // Continuous movement resets timer -> triggers ONLY when paused
        if (hoverPauseTimer) clearTimeout(hoverPauseTimer);
        hoverPauseTimer = setTimeout(function() {
          showSectorPopup(name, lastLatLng, true);
        }, 450);
      });

      layer.on('mouseout', function() {
        if (hoverPauseTimer) {
          clearTimeout(hoverPauseTimer);
          hoverPauseTimer = null;
        }
        if (selectedSectorName !== name) {
          updateLayerStyle(name);
        }
        schedulePopupClose(name);
      });

      // Click: Selects sector (filters drawer + fits bounds + pins popup)
      layer.on('click', function(e) {
        L.DomEvent.stopPropagation(e);
        if (e.originalEvent) e.originalEvent._sectorClicked = true;
        if (hoverPauseTimer) {
          clearTimeout(hoverPauseTimer);
          hoverPauseTimer = null;
        }
        openSectorDetails(name, e.latlng);
      });
    }
  }).addTo(MAP);
}

// Get Filtered Listings
function getFilteredListings() {
  return ALL_LISTINGS.filter(item => {
    // BHK Filter
    if (activeBHK !== 'all') {
      if (activeBHK === 4) {
        if (item.bhk < 4) return false;
      } else {
        if (item.bhk !== activeBHK) return false;
      }
    }

    // Furnishing Filter
    if (activeFurnishing !== 'all' && item.furnishing !== activeFurnishing) {
      return false;
    }

    // Property Type Filter
    if (activeType === 'highrise' && item.total_floors < 6) return false;
    if (activeType === 'deals' && (item.audit_anomaly_tag !== 'GENUINE_BUDGET_DEAL' || (item.total_floors && item.total_floors < 6))) return false;
    if (activeType === 'gated' && !item.is_gated_society) return false;
    if (activeType === 'builder_floor' && item.property_type !== 'Independent Builder Floor / Standalone') return false;

    // Source Portal Filter
    if (activePortal !== 'all' && item.primary_portal !== activePortal) {
      return false;
    }

    // Search Query (Society / Locality)
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      const matchSoc = (item.society || '').toLowerCase().includes(q);
      const matchLoc = (item.locality || '').toLowerCase().includes(q);
      const matchTitle = (item.title || '').toLowerCase().includes(q);
      if (!matchSoc && !matchLoc && !matchTitle) return false;
    }

    // Minimum Photos Filter (Exclude no image / single static image)
    const minPhotosEl = document.getElementById('minPhotosCheckbox');
    if (minPhotosEl && minPhotosEl.checked) {
      if ((item.photos_count || 0) < 2) return false;
    }

    return true;
  });
}

// Red Hotspot Scale (Monochromatic: Light Red for Cheaper -> Dark Red for Costlier)
let CURRENT_THRESHOLDS = { t1: 42, t2: 50, t3: 60 };

function computeDynamicThresholds(rates) {
  if (!rates || rates.length === 0) return { t1: 42, t2: 50, t3: 60 };
  if (rates.length < 4) {
    const min = rates[0];
    const max = rates[rates.length - 1];
    const step = Math.max(2, (max - min) / 4);
    return {
      t1: Math.round((min + step) * 10) / 10,
      t2: Math.round((min + 2 * step) * 10) / 10,
      t3: Math.round((min + 3 * step) * 10) / 10
    };
  }
  // 25th, 50th (median), 75th percentiles across sectors
  const q1 = rates[Math.floor(rates.length * 0.25)];
  const q2 = rates[Math.floor(rates.length * 0.50)];
  const q3 = rates[Math.floor(rates.length * 0.75)];
  let t1 = Math.round(q1 * 10) / 10;
  let t2 = Math.round(q2 * 10) / 10;
  let t3 = Math.round(q3 * 10) / 10;
  if (t2 <= t1) t2 = Math.round((t1 + 3) * 10) / 10;
  if (t3 <= t2) t3 = Math.round((t2 + 3) * 10) / 10;
  return { t1, t2, t3 };
}

function getChoroplethColor(rate, thresholds) {
  const t = thresholds || CURRENT_THRESHOLDS;
  if (rate < t.t1) return '#fca5a5'; // Light Red (Cheaper / Value)
  if (rate < t.t2) return '#f87171'; // Coral Red (Moderate)
  if (rate < t.t3) return '#dc2626'; // Vibrant Red (Prime)
  return '#881337';                 // Dark Wine Red (Costlier / Luxury)
}

function getTierInfo(rate, thresholds) {
  const t = thresholds || CURRENT_THRESHOLDS;
  if (rate < t.t1) return { label: 'Value (Cheaper)', badgeClass: 'bg-red-200/20 text-red-300 border border-red-300/40' };
  if (rate < t.t2) return { label: 'Moderate', badgeClass: 'bg-red-400/20 text-red-300 border border-red-400/40' };
  if (rate < t.t3) return { label: 'Prime', badgeClass: 'bg-red-600/20 text-red-200 border border-red-600/40' };
  return { label: 'Luxury (Costlier)', badgeClass: 'bg-rose-900/40 text-rose-200 border border-rose-800' };
}

// Update Dynamic Legend UI
function updateLegendUI(t) {
  const p1 = document.getElementById('pillT1');
  if (p1) p1.innerText = `<₹${t.t1}`;
  const p2 = document.getElementById('pillT2');
  if (p2) p2.innerText = `₹${t.t1}-${t.t2}`;
  const p3 = document.getElementById('pillT3');
  if (p3) p3.innerText = `₹${t.t2}-${t.t3}`;
  const p4 = document.getElementById('pillT4');
  if (p4) p4.innerText = `>₹${t.t3}`;

  const c1 = document.getElementById('cardT1');
  if (c1) c1.innerText = `< ₹${t.t1} / sq.ft`;
  const c2 = document.getElementById('cardT2');
  if (c2) c2.innerText = `₹${t.t1} - ₹${t.t2} / sq.ft`;
  const c3 = document.getElementById('cardT3');
  if (c3) c3.innerText = `₹${t.t2} - ₹${t.t3} / sq.ft`;
  const c4 = document.getElementById('cardT4');
  if (c4) c4.innerText = `> ₹${t.t3} / sq.ft`;
}

// Collapsible Legend Toggle
function toggleLegend() {
  const content = document.getElementById('legendContent');
  const chevron = document.getElementById('legendChevron');
  if (!content) return;
  const isHidden = content.classList.contains('hidden');
  if (isHidden) {
    content.classList.remove('hidden');
    if (chevron) chevron.innerText = '▼';
  } else {
    content.classList.add('hidden');
    if (chevron) chevron.innerText = '▲';
  }
}

// Apply Filters & Update UI
function applyFilters(closePopup = true) {
  if (closePopup && CURRENT_SECTOR_POPUP) {
    MAP.closePopup(CURRENT_SECTOR_POPUP);
    CURRENT_SECTOR_POPUP = null;
  }

  // Synchronize state from DOM elements
  const furnEl = document.getElementById('furnishingSelect');
  if (furnEl) activeFurnishing = furnEl.value;
  const typeEl = document.getElementById('typeSelect');
  if (typeEl) activeType = typeEl.value;
  const portalEl = document.getElementById('portalSelect');
  if (portalEl) activePortal = portalEl.value;
  const searchEl = document.getElementById('searchInput');
  if (searchEl) searchQuery = searchEl.value.trim();

  const filtered = getFilteredListings();

  // Aggregate stats per sector
  const statsBySector = {};
  filtered.forEach(item => {
    const sec = item.actual_sector;
    if (!statsBySector[sec]) {
      statsBySector[sec] = {
        count: 0,
        rates: [],
        costs: [],
        highrise: 0,
        societies: {}
      };
    }
    statsBySector[sec].count++;
    if (item.price_per_sqft) statsBySector[sec].rates.push(item.price_per_sqft);
    if (item.total_monthly_cost) statsBySector[sec].costs.push(item.total_monthly_cost);
    if (item.total_floors >= 6) statsBySector[sec].highrise++;

    const soc = item.society || 'Independent Flat';
    statsBySector[sec].societies[soc] = (statsBySector[sec].societies[soc] || 0) + 1;
  });

  // Calculate dynamic thresholds based on sector average rates
  const sectorAverages = Object.values(statsBySector)
    .map(s => s.rates.length ? (s.rates.reduce((a, b) => a + b, 0) / s.rates.length) : null)
    .filter(x => x !== null)
    .sort((a, b) => a - b);
  CURRENT_THRESHOLDS = computeDynamicThresholds(sectorAverages);
  updateLegendUI(CURRENT_THRESHOLDS);

  // Update Polygon Styling (Pure choropleth colors, NO hover tooltips)
  if (GEOJSON_LAYER) {
    GEOJSON_LAYER.eachLayer(layer => {
      const name = layer.feature.properties.sector_name;
      const stats = statsBySector[name];
      const isSelected = (selectedSectorName === name);

      if (name === 'Ammunition Depot') {
        layer.setStyle({
          fillColor: '#334155',
          fillOpacity: 0.35,
          color: isSelected ? '#3b82f6' : '#64748b',
          dashArray: '6, 6',
          weight: isSelected ? 3.5 : 1.5
        });
      } else if (!stats || stats.count === 0) {
        layer.setStyle({
          fillColor: '#475569',
          fillOpacity: 0.12,
          color: isSelected ? '#3b82f6' : '#64748b',
          dashArray: '4, 4',
          weight: isSelected ? 3.5 : 1.2
        });
      } else {
        const avgRate = stats.rates.length ? (stats.rates.reduce((a, b) => a + b, 0) / stats.rates.length) : 0;
        const color = getChoroplethColor(avgRate, CURRENT_THRESHOLDS);

        layer.setStyle({
          fillColor: color,
          fillOpacity: isSelected ? 0.70 : 0.45,
          color: isSelected ? '#3b82f6' : color,
          dashArray: null,
          weight: isSelected ? 3.5 : 1.8
        });
      }
    });
  }

  // Update Matching Counter Pill
  const nbTotal = filtered.filter(x => x.primary_portal === 'NoBroker').length;
  const hcTotal = filtered.filter(x => x.primary_portal === 'Housing').length;
  const naTotal = filtered.filter(x => x.primary_portal === '99acres').length;
  document.getElementById('activeCountBadge').innerText = `${filtered.length} units (${nbTotal} NB • ${hcTotal} Housing • ${naTotal} 99acres)`;

  // Update Property GPS Pins
  if (showPins) {
    renderPropertyPins(filtered);
  }

  // Update Drawer
  if (selectedSectorName) {
    renderSectorDrawer(selectedSectorName, statsBySector[selectedSectorName], filtered);
  } else {
    renderOverviewDrawer(statsBySector, filtered);
  }
}

function updateLayerStyle(sectorName) {
  if (!GEOJSON_LAYER) return;
  const filtered = getFilteredListings();
  const sectorUnits = filtered.filter(x => x.actual_sector === sectorName);
  GEOJSON_LAYER.eachLayer(layer => {
    if (layer.feature.properties.sector_name === sectorName) {
      const isSelected = (selectedSectorName === sectorName);
      if (sectorUnits.length === 0) {
        layer.setStyle({
          fillColor: '#475569',
          fillOpacity: 0.12,
          color: isSelected ? '#3b82f6' : '#64748b',
          dashArray: '4, 4',
          weight: isSelected ? 3.5 : 1.2
        });
      } else {
        const rates = sectorUnits.map(x => x.price_per_sqft).filter(Boolean);
        const avg = rates.length ? rates.reduce((a,b)=>a+b,0)/rates.length : 0;
        const color = getChoroplethColor(avg, CURRENT_THRESHOLDS);
        layer.setStyle({
          fillColor: color,
          fillOpacity: isSelected ? 0.70 : 0.45,
          color: isSelected ? '#3b82f6' : color,
          dashArray: null,
          weight: isSelected ? 3.5 : 1.8
        });
      }
    }
  });
}

function getSectorStats(secName, filteredListings) {
  const units = filteredListings.filter(x => x.actual_sector === secName);
  if (units.length === 0) return null;
  const stats = {
    count: units.length,
    rates: [],
    costs: [],
    highrise: 0,
    societies: {}
  };
  units.forEach(item => {
    if (item.price_per_sqft) stats.rates.push(item.price_per_sqft);
    if (item.total_monthly_cost) stats.costs.push(item.total_monthly_cost);
    if (item.total_floors >= 6) stats.highrise++;
    const soc = item.society || 'Independent Flat';
    stats.societies[soc] = (stats.societies[soc] || 0) + 1;
  });
  return stats;
}

// Compact HTML Generator for Sector Information Popup
function getSectorPopupHtml(name, stats) {
  let aliases = '';
  if (SECTOR_GEOJSON_DATA) {
    const feat = SECTOR_GEOJSON_DATA.features.find(f => f.properties.sector_name === name);
    if (feat && feat.properties.aliases && feat.properties.aliases.length > 0) {
      aliases = feat.properties.aliases.join(', ');
    }
  }

  if (name === 'Ammunition Depot') {
    return `
      <div class="p-1 min-w-[180px] max-w-[210px]">
        <div class="flex items-center justify-between gap-1.5 border-b border-slate-700/80 pb-1 mb-1">
          <span class="font-bold text-white text-xs truncate">Ammunition Depot</span>
          <span class="px-1.5 py-0.2 rounded text-[9px] font-bold bg-rose-950/70 text-rose-300 border border-rose-800 shrink-0">IAF Zone</span>
        </div>
        <p class="text-[10px] text-slate-300 font-semibold mb-0.5">IAF 54 ASP Defense Depot</p>
        <p class="text-[9px] text-slate-400 leading-tight">900m non-residential security perimeter. No private flats exist here.</p>
      </div>
    `;
  }

  if (!stats || stats.count === 0) {
    return `
      <div class="p-1 min-w-[170px] max-w-[200px]">
        <div class="flex items-center justify-between gap-2 border-b border-slate-700/80 pb-1 mb-1">
          <span class="font-bold text-white text-xs truncate">${name}</span>
          <span class="px-1.5 py-0.2 rounded text-[9px] font-semibold bg-slate-800 text-slate-400 border border-slate-700 shrink-0">0 Units</span>
        </div>
        ${aliases ? `<div class="text-[9px] text-slate-400 mb-1 truncate">${aliases}</div>` : ''}
        <p class="text-[10px] text-slate-400">No active units match current filters.</p>
      </div>
    `;
  }

  const avgRate = stats.rates.length ? (stats.rates.reduce((a, b) => a + b, 0) / stats.rates.length) : 0;
  const avgRent = stats.costs.length ? (stats.costs.reduce((a, b) => a + b, 0) / stats.costs.length) : 0;
  const minCost = stats.costs.length ? Math.min(...stats.costs) : 0;
  const maxCost = stats.costs.length ? Math.max(...stats.costs) : 0;
  const tier = getTierInfo(avgRate, CURRENT_THRESHOLDS);

  return `
    <div class="p-1 min-w-[200px] max-w-[230px]">
      <div class="flex items-center justify-between gap-1.5 border-b border-slate-700/80 pb-1 mb-1.5">
        <div class="min-w-0 pr-1">
          <div class="font-bold text-white text-xs leading-tight truncate">${name}</div>
          ${aliases ? `<div class="text-[9px] text-slate-400 truncate mt-0.5">${aliases}</div>` : ''}
        </div>
        <span class="px-1.5 py-0.5 rounded text-[9px] font-bold ${tier.badgeClass} shrink-0 whitespace-nowrap">${tier.label}</span>
      </div>

      <div class="flex items-center justify-between bg-slate-800/80 rounded px-2 py-1 mb-1.5 border border-slate-700/60">
        <div>
          <div class="text-[8px] text-slate-400 uppercase font-semibold leading-none mb-0.5">Avg Rate</div>
          <div class="font-bold text-amber-300 text-xs">₹${avgRate.toFixed(1)} <span class="text-[8px] font-normal text-slate-400">/sqft</span></div>
        </div>
        <div class="text-right">
          <div class="text-[8px] text-slate-400 uppercase font-semibold leading-none mb-0.5">Avg Rent</div>
          <div class="font-bold text-emerald-400 text-xs">₹${Math.round(avgRent).toLocaleString('en-IN')}</div>
        </div>
      </div>

      <div class="space-y-0.5 text-[10px] text-slate-300 mb-1.5">
        <div class="flex items-center justify-between">
          <span class="text-slate-400">Listings:</span>
          <span class="font-semibold text-white">${stats.count} flats${stats.highrise > 0 ? ` <span class="text-amber-300 font-normal">(${stats.highrise} high-rise)</span>` : ''}</span>
        </div>
        <div class="flex items-center justify-between">
          <span class="text-slate-400">Range:</span>
          <span class="font-medium text-slate-200">₹${minCost.toLocaleString('en-IN')} - ₹${maxCost.toLocaleString('en-IN')}</span>
        </div>
      </div>

      <div onclick="selectSector('${name}')" class="pt-1 border-t border-slate-700/60 flex items-center justify-between text-[9px] text-blue-400 hover:text-blue-300 font-semibold cursor-pointer transition">
        <span>View all ${stats.count} units in drawer</span>
        <span>→</span>
      </div>
    </div>
  `;
}

// Show Sector Popup (Used for both long-hover pause preview and click selection)
function showSectorPopup(name, latlng, isHoverTriggered = false) {
  if (CURRENT_SECTOR_POPUP && CURRENT_SECTOR_POPUP._sectorName === name && MAP.hasLayer(CURRENT_SECTOR_POPUP)) {
    return;
  }

  const filtered = getFilteredListings();
  const stats = getSectorStats(name, filtered);

  let sectorLayer = null;
  if (GEOJSON_LAYER) {
    GEOJSON_LAYER.eachLayer(layer => {
      if (layer.feature.properties.sector_name === name) {
        sectorLayer = layer;
      }
    });
  }

  let popupCoord = latlng;
  if (!popupCoord && sectorLayer) {
    popupCoord = sectorLayer.getBounds().getCenter();
  }
  if (!popupCoord) return;

  const popupHtml = getSectorPopupHtml(name, stats);

  if (CURRENT_SECTOR_POPUP) {
    MAP.closePopup(CURRENT_SECTOR_POPUP);
    CURRENT_SECTOR_POPUP = null;
  }

  CURRENT_SECTOR_POPUP = L.popup({
    offset: [0, -6],
    closeButton: true,
    autoClose: false,
    closeOnClick: false,
    autoPan: false,
    className: 'sector-info-popup'
  })
  .setLatLng(popupCoord)
  .setContent(popupHtml)
  .openOn(MAP);

  CURRENT_SECTOR_POPUP._sectorName = name;
  CURRENT_SECTOR_POPUP._isHoverTriggered = isHoverTriggered;

  // Grace bridge: Allow mouse to transition smoothly between sector polygon and popup
  const popupEl = CURRENT_SECTOR_POPUP.getElement();
  if (popupEl) {
    popupEl.addEventListener('mouseenter', function() {
      isCursorOverPopup = true;
      if (popupCloseTimer) {
        clearTimeout(popupCloseTimer);
        popupCloseTimer = null;
      }
    });
    popupEl.addEventListener('mouseleave', function() {
      isCursorOverPopup = false;
      schedulePopupClose(name);
    });
  }
}

// Graceful close scheduler for sector popups
function schedulePopupClose(sectorName) {
  if (popupCloseTimer) clearTimeout(popupCloseTimer);
  popupCloseTimer = setTimeout(function() {
    if (!isCursorOverPopup) {
      if (CURRENT_SECTOR_POPUP && CURRENT_SECTOR_POPUP._sectorName === sectorName) {
        MAP.closePopup(CURRENT_SECTOR_POPUP);
        CURRENT_SECTOR_POPUP = null;
      }
    }
  }, 150);
}

// Click-Driven Sector Details Handler
function openSectorDetails(name, latlng) {
  selectedSectorName = name;

  let sectorLayer = null;
  if (GEOJSON_LAYER) {
    GEOJSON_LAYER.eachLayer(layer => {
      if (layer.feature.properties.sector_name === name) {
        sectorLayer = layer;
      }
    });
  }

  if (sectorLayer) {
    MAP.fitBounds(sectorLayer.getBounds(), { padding: [40, 40], maxZoom: 15 });
  }

  // Refresh styles with selected highlight and update drawer
  applyFilters(false);

  let popupCoord = latlng;
  if (!popupCoord && sectorLayer) {
    popupCoord = sectorLayer.getBounds().getCenter();
  }

  showSectorPopup(name, popupCoord, false);
}

function focusDrawerForSector(name) {
  const drawerBody = document.getElementById('drawerBody');
  if (drawerBody) drawerBody.scrollTop = 0;
}

// Select Sector Handler (Called from drawer list or presets)
function selectSector(name) {
  openSectorDetails(name, null);
}

// Clear Sector Handler
function clearSelectedSector() {
  selectedSectorName = null;
  if (CURRENT_SECTOR_POPUP) {
    MAP.closePopup(CURRENT_SECTOR_POPUP);
    CURRENT_SECTOR_POPUP = null;
  }
  MAP.setView([28.468, 77.085], 13);
  applyFilters();
}

// Render Overview Drawer
function renderOverviewDrawer(statsBySector, filtered) {
  document.getElementById('drawerTitle').innerText = 'Gurgaon Market Overview';
  document.getElementById('drawerSubtitle').innerText = 'Click any official sector polygon to inspect verified units';
  document.getElementById('drawerBadge').innerText = `${Object.keys(statsBySector).length} Active Sectors`;
  document.getElementById('closeDrawerBtn').classList.add('hidden');

  const allRates = filtered.map(x => x.price_per_sqft).filter(Boolean);
  const avgGurgaonRate = allRates.length ? (allRates.reduce((a, b) => a + b, 0) / allRates.length).toFixed(1) : '0';
  const highRiseCount = filtered.filter(x => x.total_floors >= 6).length;

  const sortedSectors = Object.keys(statsBySector).sort((a, b) => statsBySector[b].count - statsBySector[a].count);

  let html = `
    <!-- Top Summary KPIs -->
    <div class="grid grid-cols-2 gap-2.5">
      <div class="bg-slate-800/80 border border-slate-700/80 rounded-xl p-3 shadow-sm">
        <div class="text-[11px] font-medium text-slate-400">Median Rate</div>
        <div class="text-xl font-extrabold text-blue-400 mt-0.5">₹${avgGurgaonRate} <span class="text-xs font-normal text-slate-400">/ sq.ft</span></div>
      </div>
      <div class="bg-slate-800/80 border border-slate-700/80 rounded-xl p-3 shadow-sm">
        <div class="text-[11px] font-medium text-slate-400">Verified Units</div>
        <div class="text-xl font-extrabold text-white mt-0.5">${filtered.length} <span class="text-xs font-normal text-slate-400">flats</span></div>
      </div>
      <div class="bg-slate-800/80 border border-slate-700/80 rounded-xl p-3 shadow-sm">
        <div class="text-[11px] font-medium text-slate-400">High-Rise Condos</div>
        <div class="text-xl font-extrabold text-amber-400 mt-0.5">${highRiseCount} <span class="text-xs font-normal text-slate-400">≥ 6 Fl</span></div>
      </div>
      <div class="bg-slate-800/80 border border-slate-700/80 rounded-xl p-3 shadow-sm">
        <div class="text-[11px] font-medium text-slate-400">Active Sectors</div>
        <div class="text-xl font-extrabold text-emerald-400 mt-0.5">${sortedSectors.length} <span class="text-xs font-normal text-slate-400">sectors</span></div>
      </div>
    </div>

    <!-- Portal Distribution Pill -->
    <div class="bg-slate-800/60 border border-slate-700/60 rounded-xl p-2.5 flex items-center justify-between text-xs">
      <span class="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Source Portals</span>
      <div class="flex items-center gap-2">
        <span class="px-2 py-0.5 rounded font-semibold text-red-400 bg-red-500/15 border border-red-500/30">🔴 ${filtered.filter(x => x.primary_portal === 'NoBroker').length} NoBroker</span>
        <span class="px-2 py-0.5 rounded font-semibold text-purple-400 bg-purple-500/15 border border-purple-500/30">🟣 ${filtered.filter(x => x.primary_portal === 'Housing').length} Housing.com</span>
        <span class="px-2 py-0.5 rounded font-semibold text-blue-400 bg-blue-500/15 border border-blue-500/30">🔵 ${filtered.filter(x => x.primary_portal === '99acres').length} 99acres</span>
      </div>
    </div>

    <!-- Sector Inventory & Rate Ranking -->
    <div class="bg-slate-800/60 border border-slate-700/60 rounded-xl p-3">
      <h3 class="text-xs font-bold text-slate-300 uppercase tracking-wider mb-2">Official Sector Hotspots</h3>
      <div class="space-y-1.5 max-h-72 overflow-y-auto pr-1">
  `;

  sortedSectors.forEach(secName => {
    const st = statsBySector[secName];
    const secAvg = st.rates.length ? (st.rates.reduce((a, b) => a + b, 0) / st.rates.length).toFixed(1) : '0';
    const color = getChoroplethColor(parseFloat(secAvg));

    html += `
      <div onclick="selectSector('${secName}')" class="flex items-center justify-between p-2 rounded-lg bg-slate-900/50 hover:bg-slate-700/50 cursor-pointer border border-slate-800 transition">
        <div class="flex items-center gap-2 truncate">
          <span class="w-2.5 h-2.5 rounded-full shrink-0" style="background-color: ${color}"></span>
          <span class="font-semibold text-slate-200 text-xs truncate">${secName}</span>
          <span class="text-[10px] text-slate-400 shrink-0">(${st.count} flats)</span>
        </div>
        <div class="text-right shrink-0">
          <div class="font-bold text-xs text-white">₹${secAvg} <span class="text-[10px] font-normal text-slate-400">/sqft</span></div>
          ${st.highrise > 0 ? `<span class="text-[9px] text-amber-400 font-medium">${st.highrise} high-rise</span>` : ''}
        </div>
      </div>
    `;
  });

  html += `
      </div>
    </div>

    <div class="p-3 bg-blue-900/20 border border-blue-800/40 rounded-xl text-xs text-blue-300 flex items-start gap-2.5">
      <span class="text-base shrink-0">🗺️</span>
      <div>
        <div class="font-semibold">True Municipal Boundaries</div>
        <div class="text-[11px] text-blue-200/80 mt-0.5">Every sector polygon follows official HUDA road borders with 0.0% overlap. Click any sector to inspect individual physical apartment listings with direct source URLs.</div>
      </div>
    </div>
  `;

  document.getElementById('drawerBody').innerHTML = html;
}

// Render Sector Detail Drawer
function renderSectorDrawer(secName, stats, filtered) {
  let aliases = '';
  if (SECTOR_GEOJSON_DATA) {
    const feat = SECTOR_GEOJSON_DATA.features.find(f => f.properties.sector_name === secName);
    if (feat && feat.properties.aliases) {
      aliases = feat.properties.aliases.join(', ');
    }
  }

  document.getElementById('drawerTitle').innerText = secName;
  document.getElementById('drawerSubtitle').innerText = aliases ? aliases : 'HUDA Sector Boundary';
  document.getElementById('drawerBadge').innerText = `${stats ? stats.count : 0} Matching Units`;
  document.getElementById('closeDrawerBtn').classList.remove('hidden');

  if (secName === 'Ammunition Depot') {
    document.getElementById('drawerBody').innerHTML = `
      <div class="p-4 bg-slate-800/80 border border-slate-700/80 rounded-xl space-y-3">
        <div class="flex items-center gap-2">
          <span class="text-2xl">🛡️</span>
          <div>
            <div class="font-bold text-white text-sm">IAF 54 ASP Defense Zone</div>
            <div class="text-[11px] text-rose-300 font-semibold">Government Defense Establishment</div>
          </div>
        </div>
        <p class="text-xs text-slate-300 leading-relaxed">
          The Ammunition Depot (Air Force Station Gurugram) is a statutory military facility. 
          Under Ministry of Defence notifications and Haryana Town & Country Planning (DTCP) bye-laws, 
          a 900-meter security perimeter restricts private civil and residential construction.
        </p>
        <div class="p-3 bg-slate-900/60 rounded-lg border border-slate-800 text-[11px] text-slate-400 space-y-1">
          <div>• <strong>North:</strong> Palam Vihar Extension / Carterpuri</div>
          <div>• <strong>West:</strong> Ashok Vihar (Phases 1-3 & Extn)</div>
          <div>• <strong>South:</strong> Sheetla Mata Colony / Sector 5</div>
          <div>• <strong>East:</strong> Noble Enclave / Sector 22 / Maruti Udyog</div>
        </div>
      </div>
    `;
    return;
  }

  if (!stats || stats.count === 0) {
    document.getElementById('drawerBody').innerHTML = `
      <div class="p-8 text-center text-slate-400 space-y-2">
        <div class="text-3xl">🔍</div>
        <div class="font-semibold text-slate-200">No matching listings</div>
        <div class="text-xs text-slate-400">No properties in ${secName} match active filter criteria. Try adjusting BHK, Furnishing, or Rent ceiling.</div>
        <button onclick="resetAllFilters()" class="mt-3 px-3 py-1.5 rounded-lg bg-blue-600 text-white text-xs font-semibold">Reset Filters</button>
      </div>
    `;
    return;
  }

  const avgRate = stats.rates.length ? (stats.rates.reduce((a, b) => a + b, 0) / stats.rates.length).toFixed(1) : '0';
  const avgCost = stats.costs.length ? Math.round(stats.costs.reduce((a, b) => a + b, 0) / stats.costs.length).toLocaleString() : '0';

  const sectorUnits = filtered.filter(x => x.actual_sector === secName);
  const societyList = Object.keys(stats.societies).sort((a, b) => stats.societies[b] - stats.societies[a]);

  let html = `
    <!-- KPI Summary Grid -->
    <div class="grid grid-cols-2 gap-2.5">
      <div class="bg-slate-800/80 border border-slate-700/80 rounded-xl p-2.5 shadow-sm">
        <div class="text-[11px] font-medium text-slate-400">Average Rate</div>
        <div class="text-lg font-extrabold text-blue-400 mt-0.5">₹${avgRate} <span class="text-xs font-normal text-slate-400">/ sq.ft</span></div>
      </div>
      <div class="bg-slate-800/80 border border-slate-700/80 rounded-xl p-2.5 shadow-sm">
        <div class="text-[11px] font-medium text-slate-400">Average Total Cost</div>
        <div class="text-lg font-extrabold text-emerald-400 mt-0.5">₹${avgCost} <span class="text-xs font-normal text-slate-400">/ mo</span></div>
      </div>
      <div class="bg-slate-800/80 border border-slate-700/80 rounded-xl p-2.5 shadow-sm">
        <div class="text-[11px] font-medium text-slate-400">Matching Listings</div>
        <div class="text-lg font-extrabold text-white mt-0.5">${stats.count} <span class="text-xs font-normal text-slate-400">units</span></div>
      </div>
      <div class="bg-slate-800/80 border border-slate-700/80 rounded-xl p-2.5 shadow-sm">
        <div class="text-[11px] font-medium text-slate-400">High-Rise Condos</div>
        <div class="text-lg font-extrabold text-amber-400 mt-0.5">${stats.highrise} <span class="text-xs font-normal text-slate-400">≥ 6 Fl</span></div>
      </div>
    </div>

    <!-- Society Matrix -->
    <div class="bg-slate-800/60 border border-slate-700/60 rounded-xl p-3">
      <h3 class="text-xs font-bold text-slate-300 uppercase tracking-wider mb-2">Society / Project Inventory</h3>
      <div class="space-y-1 max-h-36 overflow-y-auto pr-1 text-xs">
  `;

  societyList.slice(0, 8).forEach(socName => {
    const count = stats.societies[socName];
    html += `
      <div class="flex items-center justify-between py-1 border-b border-slate-800/80 last:border-0">
        <span class="text-slate-300 capitalize truncate max-w-[200px]">${socName}</span>
        <span class="font-mono font-semibold text-blue-300 bg-blue-900/30 px-1.5 py-0.5 rounded text-[11px]">${count} units</span>
      </div>
    `;
  });

  html += `
      </div>
    </div>

    <!-- Verified Listing Cards -->
    <div>
      <div class="flex items-center justify-between mb-1.5">
        <h3 class="text-xs font-bold text-slate-300 uppercase tracking-wider">Verified Physical Units (${sectorUnits.length})</h3>
        <span class="text-[10px] text-slate-400">Exact Floor & Link</span>
      </div>
      <div class="flex items-center gap-1.5 mb-2.5">
        <span class="px-2 py-0.5 rounded text-[10px] font-semibold bg-red-500/15 text-red-400 border border-red-500/30">🔴 ${sectorUnits.filter(x => x.primary_portal === 'NoBroker').length} NoBroker</span>
        <span class="px-2 py-0.5 rounded text-[10px] font-semibold bg-purple-500/15 text-purple-400 border border-purple-500/30">🟣 ${sectorUnits.filter(x => x.primary_portal === 'Housing').length} Housing.com</span>
        <span class="px-2 py-0.5 rounded text-[10px] font-semibold bg-blue-500/15 text-blue-400 border border-blue-500/30">🔵 ${sectorUnits.filter(x => x.primary_portal === '99acres').length} 99acres</span>
      </div>
      <div class="space-y-2.5 max-h-[460px] overflow-y-auto pr-1">
  `;

  sectorUnits.forEach(unit => {
    const isHighRise = (unit.total_floors >= 6);
    const isDeal = (unit.audit_anomaly_tag === 'GENUINE_BUDGET_DEAL');
    const portalColor = (unit.primary_portal === 'NoBroker') 
      ? 'text-red-400 border-red-500/30 bg-red-500/10' 
      : (unit.primary_portal === 'Housing')
        ? 'text-purple-400 border-purple-500/30 bg-purple-500/10'
        : 'text-blue-400 border-blue-500/30 bg-blue-500/10';
    const formattedCost = unit.total_monthly_cost ? unit.total_monthly_cost.toLocaleString() : '-';
    const formattedRate = unit.price_per_sqft ? unit.price_per_sqft.toFixed(1) : '-';

    const cardBorder = isDeal ? 'border-rose-500/50 bg-slate-800/95 shadow-md shadow-rose-950/20' : 'border-slate-700/80 hover:border-blue-500/60 bg-slate-800/90 hover:bg-slate-800 shadow-sm';

    html += `
      <div class="${cardBorder} rounded-xl p-3 transition">
        
        <div class="flex items-start justify-between gap-2">
          <div>
            <span class="px-2 py-0.5 rounded text-[10px] font-bold ${isDeal ? 'bg-rose-600/30 text-rose-300 border border-rose-500/40' : 'bg-blue-600/30 text-blue-300 border border-blue-500/40'} uppercase tracking-wide">
              ${unit.bhk} BHK
            </span>
            <span class="text-xs font-bold text-white capitalize ml-1.5">
              ${unit.society || 'Independent Flat'}
            </span>
          </div>
          <span class="px-1.5 py-0.5 rounded text-[9px] font-medium border ${portalColor}">
            ${unit.primary_portal}
          </span>
        </div>

        <!-- Rent & Rate Line -->
        <div class="mt-2 flex items-baseline justify-between">
          <div class="text-base font-extrabold text-emerald-400">
            ₹${formattedCost} <span class="text-[11px] font-normal text-slate-400">/ mo</span>
          </div>
          <div class="text-xs font-bold text-blue-300">
            ₹${formattedRate} <span class="text-[10px] font-normal text-slate-400">/ sq.ft</span>
          </div>
        </div>

        <!-- Specs Grid -->
        <div class="mt-2 pt-2 border-t border-slate-700/60 grid grid-cols-2 gap-1.5 text-[11px] text-slate-300">
          <div>📐 Area: <span class="font-semibold text-white">${unit.built_up_sqft || '-'} sq.ft</span></div>
          <div>🏢 Floor: <span class="font-semibold text-white">Floor ${unit.floor || 'G'} of ${unit.total_floors || '-'}</span></div>
          <div>🛋️ ${unit.furnishing || 'Not Specified'}</div>
          <div>🚿 ${unit.bathrooms || 1} Bathrooms</div>
        </div>

        <!-- Footer & Direct Link Button -->
        <div class="mt-3 pt-2 border-t border-slate-700/60 flex items-center justify-between gap-2">
          <div class="flex items-center gap-1.5 flex-wrap">
            ${isDeal ? '<span class="px-1.5 py-0.5 rounded text-[10px] bg-rose-500/25 text-rose-300 border border-rose-500/40 font-bold flex items-center gap-1">🔥 Top Deal (>40% Off)</span>' : ''}
            ${unit.has_direct_owner ? '<span class="px-1.5 py-0.5 rounded text-[10px] bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 font-semibold">Direct Owner</span>' : '<span class="text-[10px] text-slate-400">Agent / Verified</span>'}
            ${isHighRise ? '<span class="px-1.5 py-0.5 rounded text-[10px] bg-amber-500/20 text-amber-300 border border-amber-500/30 font-semibold">Condo (≥6 Fl)</span>' : ''}
          </div>
          
          <a href="${unit.best_listing_url}" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 px-3 py-1 rounded-lg ${isDeal ? 'bg-rose-600 hover:bg-rose-500' : 'bg-blue-600 hover:bg-blue-500'} text-white font-semibold text-xs transition shadow-sm">
            <span>View on ${unit.primary_portal}</span>
            <span class="text-[10px]">↗</span>
          </a>
        </div>

      </div>
    `;
  });

  html += `
      </div>
    </div>
  `;

  document.getElementById('drawerBody').innerHTML = html;
}

// Render Property GPS Pins
function renderPropertyPins(filtered) {
  MARKER_LAYER_GROUP.clearLayers();
  filtered.forEach(unit => {
    if (unit.latitude && unit.longitude) {
      const isDeal = (unit.audit_anomaly_tag === 'GENUINE_BUDGET_DEAL');
      const isHighRise = (unit.total_floors >= 6);
      const markerColor = isDeal ? '#f43f5e' : (isHighRise ? '#f59e0b' : '#3b82f6');
      const radius = isDeal ? 7 : 5;
      const costStr = unit.total_monthly_cost ? unit.total_monthly_cost.toLocaleString() : '-';
      const rateStr = unit.price_per_sqft ? unit.price_per_sqft.toFixed(1) : '-';

      const marker = L.circleMarker([unit.latitude, unit.longitude], {
        radius: radius,
        fillColor: markerColor,
        color: '#ffffff',
        weight: isDeal ? 2 : 1,
        opacity: 1,
        fillOpacity: 0.90
      });

      marker.bindPopup(`
        <div style="min-width: 200px; color: #0f172a; font-family: sans-serif;">
          ${isDeal ? '<div style="display: inline-block; background: #ffe4e6; color: #e11d48; font-size: 10px; font-weight: 800; padding: 2px 6px; border-radius: 4px; border: 1px solid #fecdd3; margin-bottom: 4px;">🔥 VERIFIED TOP DEAL (>40% OFF)</div>' : ''}
          <div style="font-weight: 700; font-size: 13px;">${unit.bhk} BHK in ${unit.society || 'Independent Flat'}</div>
          <div style="font-size: 11px; color: #64748b; margin-top: 2px;">${unit.actual_sector} • Floor ${unit.floor || 'G'} of ${unit.total_floors || '-'}</div>
          <div style="font-size: 14px; font-weight: 800; color: #059669; margin-top: 6px;">₹${costStr} / mo</div>
          <div style="font-size: 11px; font-weight: 600; color: #2563eb;">₹${rateStr} / sq.ft (${unit.built_up_sqft} sq.ft)</div>
          <div style="margin-top: 8px; border-top: 1px solid #e2e8f0; padding-top: 6px;">
            <a href="${unit.best_listing_url}" target="_blank" style="display: block; text-align: center; background: ${isDeal ? '#e11d48' : '#2563eb'}; color: #ffffff; text-decoration: none; font-size: 11px; font-weight: 600; padding: 5px 8px; border-radius: 6px;">View on ${unit.primary_portal} ↗</a>
          </div>
        </div>
      `);

      MARKER_LAYER_GROUP.addLayer(marker);
    }
  });
}

function togglePropertyPins(checked) {
  showPins = checked;
  if (showPins) {
    renderPropertyPins(getFilteredListings());
  } else {
    MARKER_LAYER_GROUP.clearLayers();
  }
}

// Filter Event Handlers
function setBHKFilter(bhk) {
  activeBHK = bhk;
  document.querySelectorAll('.bhk-btn').forEach(btn => {
    const val = btn.getAttribute('data-bhk');
    if (val == bhk) {
      btn.className = 'bhk-btn px-2.5 py-1 rounded-md bg-blue-600 text-white font-semibold shadow-sm transition';
    } else {
      btn.className = 'bhk-btn px-2 py-1 rounded-md text-slate-300 hover:text-white hover:bg-slate-700/50 transition';
    }
  });
  applyFilters();
}

function resetAllFilters() {
  activeBHK = 'all';
  activeFurnishing = 'all';
  activeType = 'all';
  activePortal = 'all';
  searchQuery = '';

  const furnEl = document.getElementById('furnishingSelect');
  if (furnEl) furnEl.value = 'all';
  const typeEl = document.getElementById('typeSelect');
  if (typeEl) typeEl.value = 'all';
  const portalEl = document.getElementById('portalSelect');
  if (portalEl) portalEl.value = 'all';
  const searchEl = document.getElementById('searchInput');
  if (searchEl) searchEl.value = '';

  document.querySelectorAll('.bhk-btn').forEach(btn => {
    const val = btn.getAttribute('data-bhk');
    if (val === 'all') {
      btn.className = 'bhk-btn px-2.5 py-1 rounded-md bg-blue-600 text-white font-semibold shadow-sm transition';
    } else {
      btn.className = 'bhk-btn px-2 py-1 rounded-md text-slate-300 hover:text-white hover:bg-slate-700/50 transition';
    }
  });

  selectedSectorName = null;
  applyFilters();
}

// Preset Quick Views
function applyPreset(preset) {
  resetAllFilters();
  if (preset === 'highrise') {
    const typeEl = document.getElementById('typeSelect');
    if (typeEl) typeEl.value = 'highrise';
    activeType = 'highrise';
  } else if (preset === 'deals') {
    const typeEl = document.getElementById('typeSelect');
    if (typeEl) typeEl.value = 'deals';
    activeType = 'deals';
  } else if (preset === 'sec43') {
    selectSector('Sector 43');
    return;
  } else if (preset === 'sec28') {
    selectSector('Sector 28');
    return;
  } else if (preset === 'budget2bhk') {
    setBHKFilter(2);
    return;
  }
  applyFilters();
}

// DOM Ready
window.addEventListener('DOMContentLoaded', initApp);
