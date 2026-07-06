/* Ambulance CDSS Field Console — config.js
 *
 * Runtime configuration. Override via window.AMBULANCE_CDSS_* before
 * this script loads, or set the env vars in deployment.
 */

window.AMBULANCE_CDSS_API_BASE =
  window.AMBULANCE_CDSS_API_BASE || "http://localhost:8000";

// Epic 6.2: Paramedic session storage key
window.AMBULANCE_CDSS_SESSION_KEY = "ambulance_cdss_field_session";
