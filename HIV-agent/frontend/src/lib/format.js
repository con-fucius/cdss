export function sentenceLabel(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  const normalized = text
    .replaceAll("HIV", "hiv")
    .replaceAll("AIDS", "aids")
    .replaceAll("TB", "tb")
    .replaceAll("ART", "treatment")
    .replaceAll("LLM", "model")
    .replaceAll("RAG", "rag")
    .replaceAll("HITL", "review");
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}
