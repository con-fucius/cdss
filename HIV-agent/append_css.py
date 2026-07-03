css_to_append = """
/* ===================================================
   DDX WORKSPACE (Phase H3)
   =================================================== */

.ddx-workspace {
  padding: 24px 32px;
  max-width: 1400px;
  margin: 0 auto;
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.ddx-header {
  margin-bottom: 8px;
}

.ddx-header__title {
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--text-primary);
  margin-bottom: 4px;
}

.ddx-title {
  font-size: 22px;
  font-weight: 700;
  margin: 0;
}

.ddx-subtitle {
  color: var(--text-muted);
  font-size: 14px;
  margin: 0;
}

.ddx-layout {
  display: grid;
  grid-template-columns: 320px 380px minmax(400px, 1fr);
  gap: 20px;
  align-items: flex-start;
  flex: 1;
  min-height: 0;
}

.ddx-panel {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--kini-radius, 12px);
  display: flex;
  flex-direction: column;
  height: calc(100vh - 140px);
  overflow-y: auto;
}

.ddx-panel__heading {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px 20px;
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  border-bottom: 1px solid var(--border);
  background: var(--bg-secondary);
  position: sticky;
  top: 0;
  z-index: 10;
}

/* ── Panel 1: Inputs ── */

.ddx-panel--input {
  padding-bottom: 20px;
}

.ddx-label {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  margin: 20px 20px 8px;
}

.ddx-required {
  color: var(--scoring-critical);
}

.ddx-optional {
  font-weight: 400;
  color: var(--text-muted);
  font-size: 12px;
}

.ddx-input {
  display: block;
  width: calc(100% - 40px);
  margin: 0 20px;
  padding: 10px 12px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: 13px;
  transition: all 0.2s ease;
}

.ddx-input:focus {
  border-color: var(--accent);
  outline: none;
  box-shadow: 0 0 0 2px rgba(var(--accent-rgb), 0.15);
}

.ddx-input--sm {
  width: 100px;
  margin: 0;
  padding: 6px 10px;
}

/* ── Tag Input ── */

.ddx-tag-input {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 8px;
  margin: 0 20px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  min-height: 40px;
  cursor: text;
  position: relative;
}

.ddx-tag-input:focus-within {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px rgba(var(--accent-rgb), 0.15);
}

.ddx-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  padding: 2px 8px;
  border-radius: 14px;
  font-size: 13px;
  color: var(--text-primary);
}

.ddx-tag__remove {
  display: flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  padding: 2px;
  color: var(--text-muted);
  cursor: pointer;
  border-radius: 50%;
}

.ddx-tag__remove:hover {
  color: var(--scoring-critical);
  background: var(--scoring-critical-bg);
}

.ddx-tag-input__wrapper {
  flex: 1;
  min-width: 120px;
  position: relative;
}

.ddx-tag-input__field {
  width: 100%;
  background: transparent;
  border: none;
  padding: 4px 0;
  color: var(--text-primary);
  font-size: 13px;
  outline: none;
}

.ddx-suggestions {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 4px;
  margin: 0;
  list-style: none;
  box-shadow: 0 4px 12px rgba(0,0,0,0.2);
  z-index: 50;
  max-height: 200px;
  overflow-y: auto;
}

.ddx-suggestion {
  padding: 8px 12px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
  color: var(--text-primary);
}

.ddx-suggestion:hover {
  background: var(--bg-tertiary);
  color: var(--accent);
}

/* ── Expandable Vitals/Labs ── */

.ddx-expandable {
  margin: 20px 20px 0;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  overflow: hidden;
}

.ddx-expandable__trigger {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 12px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  user-select: none;
}

.ddx-expandable__trigger:hover {
  background: var(--bg-secondary);
  color: var(--text-primary);
}

.ddx-expandable[open] .ddx-expandable__trigger svg {
  transform: rotate(90deg);
}

.ddx-expandable__content {
  padding: 12px;
  border-top: 1px solid var(--border-subtle);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.ddx-field-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.ddx-field-label {
  font-size: 12px;
  color: var(--text-secondary);
}

.ddx-run-btn {
  margin: 24px 20px 0;
  padding: 12px;
  background: var(--accent);
  color: white;
  border: none;
  border-radius: 8px;
  font-weight: 600;
  font-size: 14px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  transition: all 0.2s ease;
}

.ddx-run-btn:hover:not(:disabled) {
  filter: brightness(1.1);
  box-shadow: 0 0 12px rgba(var(--accent-rgb), 0.3);
}

.ddx-run-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  background: var(--bg-tertiary);
  color: var(--text-muted);
  border: 1px solid var(--border-subtle);
}

.ddx-spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  100% { transform: rotate(360deg); }
}

.ddx-error {
  margin: 16px 20px 0;
  padding: 12px;
  background: var(--scoring-critical-bg);
  border: 1px solid var(--scoring-critical);
  border-radius: 8px;
  color: var(--scoring-critical);
  font-size: 13px;
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

/* ── Panel 2: Candidates ── */

.ddx-panel--candidates {
  padding: 0;
}

.ddx-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 32px;
  text-align: center;
  color: var(--text-muted);
  gap: 16px;
  flex: 1;
}

.ddx-empty__icon {
  opacity: 0.3;
}

.ddx-status-msg {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 20px;
  font-size: 13px;
  color: var(--text-secondary);
  border-bottom: 1px solid var(--border-subtle);
}

.ddx-status-msg--info {
  background: var(--scoring-info-bg);
  color: var(--scoring-info-text);
  border-left: 3px solid var(--scoring-info);
  border-bottom: none;
}

.ddx-candidate {
  padding: 16px 20px;
  border-bottom: 1px solid var(--border-subtle);
  background: var(--bg-secondary);
  transition: background 0.2s ease;
}

.ddx-candidate:hover {
  background: var(--bg-tertiary);
}

.ddx-candidate__header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 8px;
}

.ddx-candidate__name {
  font-size: 15px;
  font-weight: 700;
  color: var(--text-primary);
}

.ddx-candidate__match {
  font-size: 12px;
  font-weight: 600;
  color: var(--accent);
  background: rgba(var(--accent-rgb), 0.1);
  padding: 2px 8px;
  border-radius: 12px;
}

.ddx-candidate__symptoms {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 10px;
}

.ddx-candidate__sym-tag {
  font-size: 11px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
  padding: 2px 6px;
  border-radius: 4px;
  color: var(--text-secondary);
}

.ddx-candidate__criteria {
  background: var(--bg-tertiary);
  border-left: 2px solid var(--border);
  padding: 8px 12px;
  border-radius: 0 4px 4px 0;
}

.ddx-candidate__criteria-text {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-muted);
}

/* ── Panel 3: Synthesis ── */

.ddx-panel--synthesis {
}

.ddx-done-badge {
  margin-left: auto;
  font-size: 11px;
  background: #22c55e;
  color: #fff;
  padding: 2px 8px;
  border-radius: 12px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.ddx-synthesis-content {
  padding: 20px;
}

.ddx-synthesis-text {
  margin: 0;
  font-family: var(--font-sans);
  font-size: 14px;
  line-height: 1.6;
  color: var(--text-primary);
  white-space: pre-wrap;
}

/* ===================================================
   PATHWAY EXPLORER (Phase H4)
   =================================================== */

.pathway-explorer {
  padding: 24px 32px;
  max-width: 1400px;
  margin: 0 auto;
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.pathway-explorer__header {
  display: flex;
  align-items: center;
  gap: 16px;
  color: var(--text-primary);
}

.pathway-explorer__title {
  font-size: 22px;
  font-weight: 700;
  margin: 0 0 4px;
}

.pathway-explorer__subtitle {
  color: var(--text-muted);
  font-size: 14px;
  margin: 0;
}

.pathway-notice {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  background: var(--scoring-warning-bg);
  border: 1px solid var(--scoring-warning);
  color: var(--scoring-warning-text);
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
}

.pathway-layout {
  display: grid;
  grid-template-columns: 350px 1fr;
  gap: 24px;
  flex: 1;
  min-height: 0;
}

/* ── Left Panel: List ── */

.pathway-list-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
  height: calc(100vh - 150px);
}

.pathway-filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.pathway-filter-btn {
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
  color: var(--text-secondary);
  padding: 6px 12px;
  border-radius: 16px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}

.pathway-filter-btn:hover {
  background: var(--bg-secondary);
  color: var(--text-primary);
}

.pathway-filter-btn--active {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

.pathway-filter-btn--active:hover {
  background: var(--accent);
  color: #fff;
}

.pathway-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 20px;
  color: var(--text-muted);
  font-size: 13px;
}

.pathway-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow-y: auto;
  padding-right: 4px;
}

.pathway-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 10px;
  cursor: pointer;
  text-align: left;
  transition: all 0.2s ease;
}

.pathway-item:hover:not(:disabled) {
  border-color: var(--border-subtle);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(0,0,0,0.1);
}

.pathway-item--active {
  border-color: var(--accent);
  background: rgba(var(--accent-rgb), 0.05);
}

.pathway-item--shell {
  opacity: 0.6;
  border-style: dashed;
  cursor: not-allowed;
}

.pathway-item__info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.pathway-item__name {
  font-size: 14px;
  font-weight: 700;
  color: var(--text-primary);
}

.pathway-item__disease {
  font-size: 11px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.pathway-item__meta {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-muted);
}

.pathway-item__steps {
  font-size: 12px;
  font-weight: 600;
  background: var(--bg-tertiary);
  padding: 2px 8px;
  border-radius: 12px;
}

.pathway-item__pending {
  font-size: 11px;
  font-style: italic;
}

/* ── Right Panel: Detail ── */

.pathway-detail-panel {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 12px;
  height: calc(100vh - 150px);
  overflow-y: auto;
  padding: 32px;
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.pathway-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--text-muted);
  text-align: center;
  gap: 16px;
  max-width: 300px;
  margin: 0 auto;
}

.pathway-empty__icon {
  opacity: 0.2;
}

.pathway-detail__title {
  font-size: 24px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0 0 8px;
}

.pathway-detail__population {
  font-size: 14px;
  color: var(--text-secondary);
  margin: 0;
  background: var(--bg-tertiary);
  display: inline-block;
  padding: 4px 12px;
  border-radius: 16px;
  border: 1px solid var(--border-subtle);
}

.pathway-detail__error {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 12px 16px;
  background: var(--scoring-critical-bg);
  border: 1px solid var(--scoring-critical);
  border-radius: 8px;
  color: var(--scoring-critical);
  font-size: 14px;
}

/* Contraindications */

.pathway-contraindications {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.pathway-contraindication {
  display: flex;
  align-items: center;
  gap: 10px;
  background: var(--scoring-critical-bg);
  border-left: 3px solid var(--scoring-critical);
  padding: 12px 16px;
  border-radius: 0 8px 8px 0;
  color: var(--text-primary);
  font-size: 14px;
}

.pathway-contraindication svg {
  color: var(--scoring-critical);
}

.pathway-contraindication strong {
  color: var(--scoring-critical);
}

.pathway-contraindication__ref {
  color: var(--text-muted);
  font-size: 12px;
}

/* Steps Timeline */

.pathway-steps {
  display: flex;
  flex-direction: column;
  gap: 16px;
  position: relative;
  padding-left: 28px;
}

.pathway-steps::before {
  content: '';
  position: absolute;
  left: 8px;
  top: 10px;
  bottom: 10px;
  width: 2px;
  background: var(--border-subtle);
}

.pathway-step {
  position: relative;
  display: flex;
  align-items: flex-start;
}

.pathway-step-icon {
  position: absolute;
  left: -28px;
  top: 14px;
  transform: translateX(-50%);
  background: var(--bg-secondary);
  border-radius: 50%;
  z-index: 2;
}

.pathway-step-icon--completed { color: #22c55e; }
.pathway-step-icon--current { color: var(--accent); }
.pathway-step-icon--blocked { color: #f59e0b; }
.pathway-step-icon--pending { color: var(--border); }

.pathway-step__body {
  flex: 1;
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px;
}

.pathway-step--completed .pathway-step__body { border-color: rgba(34, 197, 94, 0.3); }
.pathway-step--current .pathway-step__body { border-color: var(--accent); box-shadow: 0 4px 12px rgba(var(--accent-rgb), 0.1); }
.pathway-step--blocked .pathway-step__body { border-color: rgba(245, 158, 11, 0.3); }

.pathway-step__header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.pathway-step__num {
  font-size: 12px;
  font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase;
}

.pathway-step__name {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  flex: 1;
}

.pathway-step__badge {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 12px;
  text-transform: uppercase;
}

.pathway-step__badge--completed { background: rgba(34, 197, 94, 0.1); color: #22c55e; }
.pathway-step__badge--current { background: rgba(var(--accent-rgb), 0.1); color: var(--accent); }
.pathway-step__badge--blocked { background: rgba(245, 158, 11, 0.1); color: #f59e0b; }
.pathway-step__badge--pending { background: var(--bg-secondary); color: var(--text-muted); border: 1px solid var(--border); }

.pathway-step__ref {
  font-size: 13px;
  color: var(--text-secondary);
  margin: 0;
  line-height: 1.5;
}

.pathway-step__blocking {
  margin-top: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  background: var(--scoring-warning-bg);
  padding: 8px 12px;
  border-radius: 6px;
}

.pathway-step__blocking-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--scoring-warning-text);
}

.pathway-step__blocking-tag {
  font-size: 11px;
  font-family: var(--font-mono);
  background: var(--bg-tertiary);
  border: 1px solid var(--scoring-warning);
  color: var(--scoring-warning);
  padding: 2px 6px;
  border-radius: 4px;
}

.pathway-loading-inline {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px;
  color: var(--accent);
  font-size: 13px;
  font-weight: 500;
}

/* Summary Card */

.pathway-summary {
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 24px;
  margin-top: 16px;
}

.pathway-summary__title {
  font-size: 16px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0 0 16px;
  border-bottom: 1px solid var(--border-subtle);
  padding-bottom: 12px;
}

.pathway-summary__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 16px;
  margin-bottom: 20px;
}

.pathway-summary__stat {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-secondary);
}

.pathway-summary__monitoring {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 14px;
  font-weight: 500;
  color: var(--scoring-warning);
  grid-column: 1 / -1;
  background: var(--scoring-warning-bg);
  padding: 12px;
  border-radius: 8px;
}

.pathway-summary__actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  border-top: 1px dashed var(--border);
  padding-top: 16px;
}

.pathway-summary__actions-label {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.pathway-summary__action-tag {
  font-size: 12px;
  font-family: var(--font-mono);
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  color: var(--text-secondary);
  padding: 4px 8px;
  border-radius: 4px;
  display: inline-block;
  width: fit-content;
}

"""

with open("D:/Projects/CDSS/HIV-agent/frontend/src/supplemental.css", "a", encoding="utf-8") as f:
    f.write("\n" + css_to_append)
