// Dispatcher console configuration.
// Edit this file to point at the real API server.
// Do NOT commit credentials or internal URLs to version control.
//
// window.AMBULANCE_CDSS_API_BASE is read by app.js on startup.
// If not set here, app.js defaults to http://localhost:8000.
window.AMBULANCE_CDSS_API_BASE = "http://localhost:8000";

// EPIC 2: Map configuration
window.AMBULANCE_CDSS_MAP_CENTER = [-1.286389, 36.817223]; // Nairobi
window.AMBULANCE_CDSS_MAP_ZOOM = 13;

// Geocoding base URL for reverse/forward geocoding.
// PRODUCTION: use a self-hosted Nominatim or permitted service.
window.AMBULANCE_CDSS_GEOCODING_BASE_URL = "https://nominatim.openstreetmap.org";
