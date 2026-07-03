import React, { useState, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { motion, AnimatePresence } from 'framer-motion';
import {
  BookOpen,
  ChevronRight,
  FileText,
  MessageSquare,
  Search,
  ArrowLeft,
  ArrowUpDown,
  Info,
  ChevronLeft
} from 'lucide-react';
import { EvidencePanel } from '../chat/EvidencePanel';
import { request } from '../../lib/api';
import { sentenceLabel } from '../../lib/format';

export function GuidelinesBrowserPage({ onNavigate, diseases }) {
  const indexedDiseases = diseases.filter(disease => disease.status === 'indexed');
  const selectableDiseases = indexedDiseases.length > 0 ? indexedDiseases : diseases;
  const [selectedDisease, setSelectedDisease] = useState(selectableDiseases[0]?.id || '');
  const [toc, setToc] = useState([]);
  const [selectedSection, setSelectedSection] = useState(null);
  const [sectionContent, setSectionContent] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [sortMode, setSortMode] = useState('page_asc');
  const [jumpPage, setJumpPage] = useState('');
  const [pageIndexQuery, setPageIndexQuery] = useState('');
  const [pageIndexResults, setPageIndexResults] = useState([]);
  const [pageIndexLoading, setPageIndexLoading] = useState(false);
  const [pageIndexError, setPageIndexError] = useState('');
  const [evidenceQuery, setEvidenceQuery] = useState('');
  const [evidenceResults, setEvidenceResults] = useState([]);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [evidenceError, setEvidenceError] = useState('');

  const displayToc = toc.map((item, index) => {
    const duplicateCount = toc.filter(candidate => candidate.title === item.title).length;
    return {
      ...item,
      displayTitle: duplicateCount > 1 ? `${item.title} · chunk ${index + 1}` : item.title
    };
  });

  useEffect(() => {
    if (selectableDiseases.length > 0 && !selectableDiseases.some(disease => disease.id === selectedDisease)) {
      setSelectedDisease(selectableDiseases[0].id);
    }
  }, [selectableDiseases, selectedDisease]);

  useEffect(() => {
    if (!selectedDisease) return;
    setIsLoading(true);
    setError('');
    request(`/guidelines/${selectedDisease}/toc`)
      .then(data => {
        setToc(data.toc || []);
        setIsLoading(false);
      })
      .catch(e => {
        console.error(e);
        setError(e.message || 'Unable to load guideline sections');
        setToc([]);
        setIsLoading(false);
      });
  }, [selectedDisease]);

  const handleSectionClick = async (sectionId) => {
    setSelectedSection(sectionId);
    setSectionContent(null);
    setIsLoading(true);
    setError('');
    try {
      const data = await request(`/guidelines/${selectedDisease}/section/${sectionId}`);
      setSectionContent(data);
    } catch (e) {
      console.error(e);
      setError(e.message || 'Unable to load section');
    } finally {
      setIsLoading(false);
    }
  };

  const handleAskAboutSection = () => {
    if (!sectionContent) return;
    const diseaseName = sentenceLabel(diseases.find(d => d.id === selectedDisease)?.display_name || selectedDisease);
    const query = `[Context: viewing ${diseaseName} guidelines, section: ${sectionContent.title}]\nExplain the key points of this section.`;
    onNavigate('/chat', query);
  };

  const runPageIndexQuery = async () => {
    const query = pageIndexQuery.trim();
    if (!query || !selectedDisease) return;
    setPageIndexLoading(true);
    setPageIndexError('');
    try {
      const data = await request('/pageindex/query', {
        method: 'POST',
        body: JSON.stringify({ query, disease: selectedDisease, top_k: 5 })
      });
      setPageIndexResults(data.results || []);
    } catch (error) {
      setPageIndexResults([]);
      setPageIndexError(error.message || 'Unable to query PageIndex');
    } finally {
      setPageIndexLoading(false);
    }
  };

  const runEvidenceQuery = async () => {
    const query = evidenceQuery.trim();
    if (!query || !selectedDisease) return;
    setEvidenceLoading(true);
    setEvidenceError('');
    try {
      const data = await request('/evidence/query', {
        method: 'POST',
        body: JSON.stringify({ query, disease: selectedDisease, top_k: 5 })
      });
      setEvidenceResults(data.results || []);
    } catch (error) {
      setEvidenceResults([]);
      setEvidenceError(error.message || 'Unable to query evidence graph');
    } finally {
      setEvidenceLoading(false);
    }
  };

  const evidenceTriples = evidenceResults.map(item => ({
    source: item.source_node?.label,
    relation: item.edge?.relation_type,
    target: item.target_node?.label,
    source_ref: item.edge?.source_ref,
    score: item.score
  })).filter(item => item.source && item.relation && item.target);

  const filteredToc = displayToc
    .filter(item => item.displayTitle.toLowerCase().includes(searchQuery.toLowerCase()))
    .sort((a, b) => {
      if (sortMode === 'page_desc') return Number(b.page || 0) - Number(a.page || 0);
      if (sortMode === 'title_asc') return a.displayTitle.localeCompare(b.displayTitle);
      return Number(a.page || 0) - Number(b.page || 0);
    });

  const selectedIndex = filteredToc.findIndex(item => item.id === selectedSection);
  const navigateRelative = (offset) => {
    const next = filteredToc[selectedIndex + offset];
    if (next) handleSectionClick(next.id);
  };

  const handleJumpToPage = () => {
    const page = Number(jumpPage);
    if (!Number.isFinite(page) || page < 1) return;
    const match = toc.find(item => Number(item.page) === page) || toc.find(item => String(item.id).includes(`page-${page}`));
    if (match) handleSectionClick(match.id);
  };

  const selectedDiseaseName = sentenceLabel(diseases.find(d => d.id === selectedDisease)?.display_name || selectedDisease);

  return (
    <div className="page-wrapper">
      <div className="browser-container">
        <aside className="browser-sidebar">
          <div className="sidebar-header-compact">
            <label className="input-label-small">Clinical guideline</label>
            <div className="select-container">
              <select
                className="browser-select-modern"
                value={selectedDisease}
                onChange={e => { setSelectedDisease(e.target.value); setSelectedSection(null); setSectionContent(null); }}
              >
                {selectableDiseases.map(d => (
                  <option key={d.id} value={d.id}>
                    {sentenceLabel(d.display_name)}{d.status !== 'indexed' ? ' (not indexed)' : ''}
                  </option>
                ))}
              </select>
              <div className="select-arrow"><ChevronRight size={14} /></div>
            </div>
          </div>

          <div className="search-bar-inline">
            <Search size={14} className="search-icon" />
            <input
              type="text"
              placeholder="Filter sections..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>

          <div className="guideline-tools-row">
            <select value={sortMode} onChange={event => setSortMode(event.target.value)} aria-label="Sort sections">
              <option value="page_asc">Page low to high</option>
              <option value="page_desc">Page high to low</option>
              <option value="title_asc">Title a to z</option>
            </select>
            <div className="jump-control">
              <input
                value={jumpPage}
                onChange={event => setJumpPage(event.target.value)}
                onKeyDown={event => event.key === 'Enter' && handleJumpToPage()}
                placeholder="Page"
                inputMode="numeric"
              />
              <button onClick={handleJumpToPage}>Go</button>
            </div>
          </div>

          <nav className="toc-nav">
            <div className="toc-header">
              <span>Table of contents</span>
            </div>
            <div className="toc-list-modern">
              {isLoading && !selectedSection && (
                <div className="toc-skeleton">
                  {[1, 2, 3, 4, 5, 6].map(i => <div key={i} className="skeleton-item" />)}
                </div>
              )}
              {!isLoading && filteredToc.length === 0 && (
                <div className="toc-empty-state">{error || 'No matching sections.'}</div>
              )}
              {filteredToc.map(item => (
                <button
                  key={item.id}
                  className={`toc-link ${selectedSection === item.id ? 'active' : ''}`}
                  onClick={() => handleSectionClick(item.id)}
                >
                  <FileText size={14} />
                  <span className="toc-text">{item.displayTitle}</span>
                  {selectedSection === item.id && <motion.div layoutId="active-toc" className="active-indicator" />}
                </button>
              ))}
            </div>
          </nav>

          <section className="browser-backend-tools">
            <div className="toc-header">
              <span>Backend retrieval tools</span>
            </div>
            <div className="browser-tool-block">
              <label className="browser-tool-label">
                <span>PageIndex</span>
                <input
                  type="text"
                  value={pageIndexQuery}
                  onChange={event => setPageIndexQuery(event.target.value)}
                  onKeyDown={event => event.key === 'Enter' && runPageIndexQuery()}
                  placeholder="Search page summaries..."
                />
              </label>
              <button className="btn-ghost-sm" onClick={runPageIndexQuery} disabled={pageIndexLoading}>
                {pageIndexLoading ? 'Searching...' : 'Run'}
              </button>
              {pageIndexError && (
                <div className="inline-warning compact">
                  <AlertCircle size={13} />
                  <span>{pageIndexError}</span>
                </div>
              )}
              <div className="toc-list-modern compact-list">
                {pageIndexResults.map((item, index) => (
                  <div className="toc-result-card" key={`${item.disease}-${item.page}-${index}`}>
                    <strong>p. {item.page || 'n/a'} · {sentenceLabel(item.disease)}</strong>
                    <span>{item.summary || item.text?.slice(0, 180)}</span>
                    <small>score {Number(item.score || 0).toFixed(2)}</small>
                  </div>
                ))}
                {!pageIndexLoading && pageIndexResults.length === 0 && !pageIndexError && (
                  <div className="toc-empty-state">No PageIndex search run yet.</div>
                )}
              </div>
            </div>

            <div className="browser-tool-block">
              <label className="browser-tool-label">
                <span>Evidence graph</span>
                <input
                  type="text"
                  value={evidenceQuery}
                  onChange={event => setEvidenceQuery(event.target.value)}
                  onKeyDown={event => event.key === 'Enter' && runEvidenceQuery()}
                  placeholder="Query graph triples..."
                />
              </label>
              <button className="btn-ghost-sm" onClick={runEvidenceQuery} disabled={evidenceLoading}>
                {evidenceLoading ? 'Searching...' : 'Run'}
              </button>
              {evidenceError && (
                <div className="inline-warning compact">
                  <AlertCircle size={13} />
                  <span>{evidenceError}</span>
                </div>
              )}
              <EvidencePanel triples={evidenceTriples} />
            </div>
          </section>
        </aside>

        <main className="browser-content-modern">
          <AnimatePresence mode="wait">
            {isLoading && selectedSection ? (
              <motion.div
                key="loading"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="content-loading-state"
              >
                <div className="skeleton-title-large" />
                <div className="skeleton-text-block" />
                <div className="skeleton-text-block" />
              </motion.div>
            ) : sectionContent ? (
              <motion.div
                key={selectedSection}
                initial={{ opacity: 0, x: 20 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -20 }}
                className="section-container"
              >
                <header className="section-content-header">
                  <div className="header-info">
                    <span className="disease-tag">{selectedDiseaseName}</span>
                    <h1 className="section-title">{sectionContent.title}</h1>
                  </div>
                  <button className="btn-action-primary" onClick={handleAskAboutSection}>
                    <MessageSquare size={16} />
                    <span>Analyze section</span>
                  </button>
                </header>

                <article className="markdown-reader">
                  <div className="source-banner">
                    <Info size={14} />
                    <span>Source: {sectionContent.source}</span>
                  </div>
                  <div className="markdown-body-premium">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {sectionContent.text}
                    </ReactMarkdown>
                  </div>
                </article>

                <footer className="section-footer">
                  <button className="btn-ghost" onClick={() => navigateRelative(-1)} disabled={selectedIndex <= 0}>
                    <ChevronLeft size={14} />
                    <span>Previous</span>
                  </button>
                  <button className="btn-ghost" onClick={() => navigateRelative(1)} disabled={selectedIndex < 0 || selectedIndex >= filteredToc.length - 1}>
                    <ArrowUpDown size={14} />
                    <span>Next</span>
                  </button>
                  <button className="btn-ghost" onClick={() => setSelectedSection(null)}>
                    <ArrowLeft size={14} />
                    <span>Back to overview</span>
                  </button>
                </footer>
              </motion.div>
            ) : (
              <motion.div
                key="placeholder"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="browser-placeholder-state"
              >
                <div className="placeholder-visual">
                  <BookOpen size={64} strokeWidth={1} />
                </div>
                <h2>Explore the {selectedDiseaseName} guidelines</h2>
                <p>Select a section from the table of contents to view clinical details, protocols, and diagnostic criteria.</p>
                <div className="placeholder-actions">
                  <button className="btn-outline-md" onClick={() => toc[0]?.id && handleSectionClick(toc[0].id)} disabled={!toc[0]?.id}>
                    Start reading
                  </button>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </main>
      </div>
    </div>
  );
}
