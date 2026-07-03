import React from 'react';

const SAMPLE_QUESTIONS = [
  { id: 'general', text: "What is the recommended screening protocol?", disease: "general" },
  { id: 'hiv_1', text: "What are the first-line treatment regimens for adults?", disease: "hiv" },
  { id: 'hiv_2', text: "How should pregnant women with hiv be treated?", disease: "hiv" },
  { id: 'dm_1', text: "What is the diagnostic criteria for type 2 diabetes?", disease: "diabetes" },
  { id: 'dm_2', text: "When should insulin be initiated in type 2 diabetes?", disease: "diabetes" },
  { id: 'cvd_1', text: "What are the blood pressure targets for patients with hypertension?", disease: "cvd" },
  { id: 'tb_1', text: "What is the intensive phase regimen for drug-susceptible tb?", disease: "tb" },
  { id: 'malaria_1', text: "What is the first-line treatment for uncomplicated malaria in adults?", disease: "malaria" },
  { id: 'malaria_2', text: "How is severe malaria managed in a child?", disease: "malaria" },
  { id: 'malaria_3', text: "What is the recommended malaria treatment in pregnancy?", disease: "malaria" },
  { id: 'comorbid_1', text: "How do you manage a patient with hiv and tb co-infection?", disease: "general" },
];

export function SmartSuggestions({ suggestions, onSelect }) {
  if (!suggestions || suggestions.length === 0) return null;

  const getSuggestionText = (s) => typeof s === 'string' ? s : s.text;
  return (
    <div className="smart-suggestions" role="list" aria-label="Suggestions">
      {suggestions.map((suggestion, i) => (
        <button
          key={i}
          className="suggestion-btn"
          onClick={() => onSelect(getSuggestionText(suggestion))}
          role="listitem"
        >
          {getSuggestionText(suggestion)}
        </button>
      ))}
    </div>
  );
}

export function AutoSuggestOnFocus({ onSelect }) {
  return (
    <div
      className="auto-suggest"
      role="list"
      aria-label="Sample questions"
    >
      {SAMPLE_QUESTIONS.map((q, i) => (
        <button
          key={i}
          className="auto-suggest-btn"
          onClick={() => onSelect(q.text)}
          role="listitem"
        >
          {q.text}
        </button>
      ))}
    </div>
  );
}

export function HITLComponent({ hitl, onRespond }) {
  if (!hitl) return null;

  return (
    <div className="hitl-container" role="dialog" aria-label="Human in the loop">
      <div className="hitl-question">{hitl.text}</div>
      <div className="hitl-options">
        {hitl.options.map((opt, i) => (
          <button
            key={i}
            className="hitl-option"
            onClick={() => onRespond(opt)}
          >
            {opt}
          </button>
        ))}
      </div>
    </div>
  );
}
