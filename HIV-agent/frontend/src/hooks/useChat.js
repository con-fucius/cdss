import { useState, useEffect, useCallback } from "react";
import { useToast } from "../context/ToastContext";
import { request, streamRequest } from "../lib/api";

const STORAGE_KEYS = {
  messages: "kini_messages",
  reactions: "kini_reactions",
  feedback: "kini_feedback",
  feedbackGiven: "kini_feedback_given",
  pinned: "kini_pinned",
  conversations: "kini_conversations",
  settings: "kini_settings",
};

const loadFromStorage = (key) => {
  try {
    const legacyKey = key.replace("kini_", "kiniq_");
    const saved =
      sessionStorage.getItem(key) ||
      sessionStorage.getItem(legacyKey) ||
      localStorage.getItem(key) ||
      localStorage.getItem(legacyKey);
    if (saved && !sessionStorage.getItem(key)) {
      sessionStorage.setItem(key, saved);
    }
    localStorage.removeItem(key);
    localStorage.removeItem(legacyKey);
    sessionStorage.removeItem(legacyKey);
    return saved ? JSON.parse(saved) : null;
  } catch (_error) {
    console.error(`Failed to load ${key}`, _error);
    return null;
  }
};

const saveToStorage = (key, data) => {
  try {
    sessionStorage.setItem(key, JSON.stringify(data));
    localStorage.removeItem(key);
    localStorage.removeItem(key.replace("kini_", "kiniq_"));
  } catch (_error) {
    console.error(`Failed to save ${key}`, _error);
  }
};

const generateId = () => crypto.randomUUID();

const summarizeTitle = (text) => {
  const cleaned = String(text || "")
    .replace(/\s+/g, " ")
    .trim();
  return cleaned.length > 64
    ? `${cleaned.slice(0, 61)}...`
    : cleaned || "Clinical session";
};

export function useChat(
  sessionId,
  initialQuery,
  patientContext,
  _settings,
  patientRefHash,
) {
  const [messages, setMessages] = useState(
    () => loadFromStorage(STORAGE_KEYS.messages) || [],
  );
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isInitialized, setIsInitialized] = useState(false);
  const [agentActions, setAgentActions] = useState([]);
  const [suggestions, setSuggestions] = useState([]);
  const [reactions, setReactions] = useState(
    () => loadFromStorage(STORAGE_KEYS.reactions) || {},
  );
  const [feedbackGiven, setFeedbackGiven] = useState(
    () => loadFromStorage(STORAGE_KEYS.feedbackGiven) || {},
  );
  const [pinnedMessages, setPinnedMessages] = useState(
    () => loadFromStorage(STORAGE_KEYS.pinned) || [],
  );
  const [conversations, setConversations] = useState(
    () => loadFromStorage(STORAGE_KEYS.conversations) || [],
  );
  const [currentConvId, setCurrentConvId] = useState(null);
  const [sessionStatus, setSessionStatus] = useState("connecting");
  const [isOfflineMode, setIsOfflineMode] = useState(false);
  const [health, setHealth] = useState(null);
  const [hitl, setHitl] = useState(null);

  const toast = useToast();

  // Storage sync
  useEffect(() => {
    saveToStorage(STORAGE_KEYS.messages, messages);
  }, [messages]);
  useEffect(() => {
    saveToStorage(STORAGE_KEYS.reactions, reactions);
  }, [reactions]);
  useEffect(() => {
    saveToStorage(STORAGE_KEYS.feedbackGiven, feedbackGiven);
  }, [feedbackGiven]);
  useEffect(() => {
    saveToStorage(STORAGE_KEYS.pinned, pinnedMessages);
  }, [pinnedMessages]);
  useEffect(() => {
    saveToStorage(STORAGE_KEYS.conversations, conversations);
  }, [conversations]);

  // Health check
  useEffect(() => {
    const checkInit = async () => {
      try {
        const data = await request("/health");
        const usable = data.status === "ok" || data.status === "degraded";
        setHealth(data);
        setIsInitialized(usable);
        setSessionStatus(usable ? "connected" : "connecting");
        setIsOfflineMode(data.mode === "kb_only");
      } catch (_error) {
        setHealth(null);
        setSessionStatus("disconnected");
        setIsOfflineMode(false);
      }
    };
    checkInit();
    const interval = setInterval(checkInit, 5000);
    return () => clearInterval(interval);
  }, []);

  const addAgentAction = (text, detail = "") =>
    setAgentActions((prev) => [
      ...prev,
      { text, detail, done: false, time: new Date().toISOString() },
    ]);
  const completeAgentAction = (text) =>
    setAgentActions((prev) =>
      prev.map((a) => (a.text === text ? { ...a, done: true } : a)),
    );

  const handleSend = useCallback(
    async (text, isRegeneration = false) => {
      if (!text.trim() || isLoading) return;

      if (!isRegeneration) {
        const userMsg = {
          role: "user",
          content: text,
          timestamp: new Date().toISOString(),
          id: generateId(),
        };
        setMessages((prev) => [...prev, userMsg]);
      }

      setInput("");
      setIsLoading(true);
      setAgentActions([]);
      setSuggestions([]);
      setHitl(null);

      try {
        const body = {
          session_id: sessionId,
          message: text,
          context: patientContext,
        };
        if (patientRefHash) body.patient_ref_hash = patientRefHash;
        const response = await streamRequest("/chat/stream", {
          method: "POST",
          body: JSON.stringify(body),
        });

        if (!response.ok)
          throw new Error(
            `Failed to connect to clinical agent (HTTP ${response.status})`,
          );
        if (!response.body)
          throw new Error("Clinical agent response stream is unavailable");

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let assistantContent = "";
        let buffered = "";
        const msgId = generateId();

        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            content: "",
            timestamp: new Date().toISOString(),
            sources: [],
            concepts: [],
            triples: [],
            interactions: [],
            reasoning: [],
            drugInteractionStatus: null,
            id: msgId,
          },
        ]);

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffered += decoder.decode(value, { stream: true });
          const events = buffered.split("\n\n");
          buffered = events.pop() || "";

          for (const event of events) {
            const dataLines = event
              .split("\n")
              .filter((line) => line.startsWith("data: "))
              .map((line) => line.slice(6));

            for (const payload of dataLines) {
              if (payload.trim()) {
                try {
                  const data = JSON.parse(payload);
                  if (data.type === "loading" || data.type === "activity") {
                    addAgentAction(data.message, data.detail || "");
                  } else if (data.type === "tool_call") {
                    addAgentAction(
                      `${data.tool_name}: ${data.query || "running"}`,
                      data.detail || "",
                    );
                  } else if (data.type === "chunk") {
                    assistantContent += data.content;
                    setMessages((prev) => {
                      const newMessages = [...prev];
                      newMessages[newMessages.length - 1].content =
                        assistantContent;
                      return newMessages;
                    });
                  } else if (data.type === "done") {
                    completeAgentAction("Processing clinical query");
                    if (data.latency_ms)
                      addAgentAction(
                        "Response completed",
                        `${Math.round(data.latency_ms)} ms`,
                      );
                  } else if (data.type === "sources") {
                    setMessages((prev) => {
                      const newMessages = [...prev];
                      newMessages[newMessages.length - 1].sources =
                        data.sources;
                      return newMessages;
                    });
                  } else if (data.type === "concepts") {
                    setMessages((prev) => {
                      const newMessages = [...prev];
                      newMessages[newMessages.length - 1].concepts =
                        data.concepts || [];
                      return newMessages;
                    });
                  } else if (data.type === "evidence") {
                    setMessages((prev) => {
                      const newMessages = [...prev];
                      newMessages[newMessages.length - 1].triples =
                        data.triples || [];
                      return newMessages;
                    });
                  } else if (data.type === "drug_interactions") {
                    setMessages((prev) => {
                      const newMessages = [...prev];
                      newMessages[newMessages.length - 1].interactions =
                        data.interactions || [];
                      newMessages[
                        newMessages.length - 1
                      ].drugInteractionStatus = data;
                      return newMessages;
                    });
                  } else if (data.type === "clinical_score") {
                    setMessages((prev) => {
                      const newMessages = [...prev];
                      const last = newMessages[newMessages.length - 1];
                      last.clinicalScores = [
                        ...(last.clinicalScores || []),
                        ...(data.scores || []),
                      ];
                      return newMessages;
                    });
                  } else if (data.type === "hitl_prompt") {
                    setHitl(data.hitl);
                  } else if (data.type === "reasoning") {
                    setMessages((prev) => {
                      const newMessages = [...prev];
                      const last = newMessages[newMessages.length - 1];
                      last.reasoning = [
                        ...(last.reasoning || []),
                        data.summary ||
                          data.content ||
                          "Provider returned reasoning metadata",
                      ];
                      return newMessages;
                    });
                    addAgentAction(
                      "Reasoning summary",
                      data.summary ||
                        data.content ||
                        "Provider returned reasoning metadata",
                    );
                  } else if (data.type === "error") {
                    toast.addToast(data.message, "error");
                    setMessages((prev) => {
                      const newMessages = [...prev];
                      newMessages[newMessages.length - 1] = {
                        ...newMessages[newMessages.length - 1],
                        isError: true,
                        content: assistantContent || data.message,
                      };
                      return newMessages;
                    });
                  }
                } catch (_error) {
                  /* ignore chunking noise */
                }
              }
            }
          }
        }
      } catch (error) {
        toast.addToast(error.message, "error");
      } finally {
        setIsLoading(false);
      }
    },
    [sessionId, patientContext, patientRefHash, isLoading, toast],
  );

  useEffect(() => {
    if (!messages.length) return;
    const firstUserMessage = messages.find(
      (message) => message.role === "user",
    );
    const conversationId = currentConvId || sessionId;
    const conversation = {
      id: conversationId,
      title: summarizeTitle(firstUserMessage?.content),
      messages,
      updatedAt: new Date().toISOString(),
    };
    setCurrentConvId(conversationId);
    setConversations((prev) => {
      const rest = prev.filter((item) => item.id !== conversationId);
      return [...rest, conversation].slice(-20);
    });
  }, [currentConvId, messages, sessionId]);

  const handleNewConversation = useCallback(() => {
    const body = { patient_context: patientContext };
    if (patientRefHash) body.patient_ref_hash = patientRefHash;
    request(`/sessions/${sessionId}/clear`, {
      method: "POST",
      body: JSON.stringify(body),
    }).catch((error) => toast.addToast(error.message, "error", 5000));
    setMessages([]);
    setAgentActions([]);
    setHitl(null);
    setCurrentConvId(crypto.randomUUID());
    toast.addToast("New clinical session started", "info");
  }, [sessionId, patientContext, patientRefHash, toast]);

  const selectConversation = useCallback(
    (conversationId) => {
      const conversation = conversations.find(
        (item) => item.id === conversationId,
      );
      setCurrentConvId(conversationId);
      if (conversation?.messages) {
        setMessages(conversation.messages);
        setAgentActions([]);
        setHitl(null);
      }
    },
    [conversations],
  );

  const submitFeedback = useCallback(
    async ({ message, feedbackType, note = "", correction = "" }) => {
      const sourcesUsed = (message.sources || [])
        .map((source) =>
          typeof source === "string"
            ? source
            : source.source || source.chunk_id || "",
        )
        .filter(Boolean);

      await request("/feedback", {
        method: "POST",
        body: JSON.stringify({
          session_id: sessionId,
          message_id: message.id,
          feedback_type: feedbackType,
          note,
          correction,
          sources_used: sourcesUsed,
        }),
      });
    },
    [sessionId],
  );

  return {
    messages,
    setMessages,
    input,
    setInput,
    isLoading,
    isInitialized,
    agentActions,
    suggestions,
    sessionStatus,
    isOfflineMode,
    health,
    hitl,
    handleSend,
    handleNewConversation,
    submitFeedback,
    pinnedMessages,
    setPinnedMessages,
    conversations,
    setConversations,
    currentConvId,
    setCurrentConvId: selectConversation,
    feedbackGiven,
    setFeedbackGiven,
    reactions,
    setReactions,
  };
}
