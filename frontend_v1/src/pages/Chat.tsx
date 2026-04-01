import { useState, useRef, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import ReactMarkdown from "react-markdown";

const API_BASE = "http://127.0.0.1:8000";

interface Message {
  role: "user" | "bot";
  text: string;
}

interface Session {
  id: number;
  title: string;
  query: string;
  answer: string;
}

type AIState = "idle" | "thinking" | "checking_sources" | "generating" | "error";

const SUGGESTIONS = [
  "What are Arun Kumar's current symptoms?",
  "Show blood report for visit V00023",
  "Compare P001's weight across all visits",
  "Any allergies for Priya Sharma?",
];

function aiStateText(state: AIState): string {
  switch (state) {
    case "thinking": return "I'm currently thinking...";
    case "checking_sources": return "Checking external resources...";
    case "generating": return "Generating an answer for you...";
    case "error": return "Something went wrong...";
    default: return "";
  }
}

const Chat = () => {
  const navigate = useNavigate();
  const storedUser = sessionStorage.getItem("scb_user");

  useEffect(() => {
    if (!storedUser) navigate("/");
  }, [storedUser, navigate]);

  const [messages, setMessages] = useState<Message[]>([]);
  const [sessions, setSessions] = useState<Session[]>(() =>
    JSON.parse(sessionStorage.getItem("scb_sessions") || "[]")
  );
  const [activeSession, setActiveSession] = useState<number | null>(null);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [showWelcome, setShowWelcome] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [streamingText, setStreamingText] = useState<string | null>(null);
  const [aiState, setAiState] = useState<AIState>("idle");

  // Voice input state
  const [isRecording, setIsRecording] = useState(false);
  const [isRecognitionReady, setIsRecognitionReady] = useState(false);
  const recognitionRef = useRef<any>(null);
  const isManualStopRef = useRef(false);
  const currentTranscriptRef = useRef("");

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const streamIdRef = useRef<string | null>(null);

  const scrollBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  useEffect(() => { scrollBottom(); }, [messages, streamingText, scrollBottom]);

  // Initialize Web Speech API
  useEffect(() => {
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) {
      console.warn("Web Speech API not supported in this browser.");
      setIsRecognitionReady(false);
      return;
    }

    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = "en-US";

    recognition.onresult = (event: any) => {
      let finalTranscript = "";
      let interimTranscript = "";

      for (let i = event.resultIndex; i < event.results.length; i++) {
        const segment = event.results[i][0].transcript;
        if (event.results[i].isFinal) {
          finalTranscript += segment + " ";
        } else {
          interimTranscript += segment;
        }
      }

      if (finalTranscript) {
        currentTranscriptRef.current += finalTranscript;
      }

      const combined = (currentTranscriptRef.current + interimTranscript).trim();
      if (combined) {
        setInput(combined);
        autoResize();
      }
    };

    recognition.onstart = () => {
      setIsRecording(true);
      currentTranscriptRef.current = "";
    };

    recognition.onend = () => {
      setIsRecording(false);
      if (!isManualStopRef.current) {
        try { recognition.start(); } catch { /* ignore */ }
      }
      isManualStopRef.current = false;
    };

    recognition.onerror = (event: any) => {
      console.error("Speech recognition error:", event.error);
      setIsRecording(false);
      isManualStopRef.current = false;
    };

    recognitionRef.current = recognition;
    setIsRecognitionReady(true);
  }, []);

  const toggleRecording = async () => {
    if (!recognitionRef.current) return;

    if (isRecording) {
      isManualStopRef.current = true;
      recognitionRef.current.stop();
    } else {
      try {
        await navigator.mediaDevices.getUserMedia({ audio: true });
        currentTranscriptRef.current = "";
        setInput("");
        recognitionRef.current.start();
      } catch {
        alert("Unable to access microphone. Please check permissions.");
      }
    }
  };

  const stopGeneration = useCallback(async () => {
    const sid = streamIdRef.current;
    if (!sid) return;
    const token = sessionStorage.getItem("scb_token");
    try {
      await fetch(`${API_BASE}/chat/stop/${sid}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
    } catch { /* server may have already cleaned up */ }
    streamIdRef.current = null;
  }, []);

  const autoResize = () => {
    const el = textareaRef.current;
    if (el) { el.style.height = "auto"; el.style.height = Math.min(el.scrollHeight, 160) + "px"; }
  };

  const saveSession = useCallback((query: string, answer: string) => {
    const session: Session = {
      id: Date.now(),
      title: query.length > 42 ? query.slice(0, 42) + "…" : query,
      query,
      answer,
    };
    setSessions(prev => {
      const updated = [session, ...prev].slice(0, 20);
      sessionStorage.setItem("scb_sessions", JSON.stringify(updated));
      return updated;
    });
  }, []);

  const sendMessage = useCallback(async (queryOverride?: string) => {
    const query = (queryOverride || input).trim();
    if (!query || isStreaming) return;

    // Stop recording if active
    if (isRecording && recognitionRef.current) {
      isManualStopRef.current = true;
      recognitionRef.current.stop();
    }

    setShowWelcome(false);
    setInput("");
    setIsStreaming(true);
    setAiState("thinking");
    setMessages(prev => [...prev, { role: "user", text: query }]);
    setStreamingText("");

    try {
      const token = sessionStorage.getItem("scb_token");
      const res = await fetch(`${API_BASE}/chat/stream`, {
        method: "POST",
        mode: "cors",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ query }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        setStreamingText(null);
        setAiState("error");
        setMessages(prev => [...prev, { role: "bot", text: `⚠️ ${err.detail || "An error occurred."}` }]);
        setIsStreaming(false);
        setTimeout(() => setAiState("idle"), 3000);
        return;
      }

      setAiState("generating");
      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let fullText = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop()!;

        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith("data:")) continue;
          let json: any;
          try { json = JSON.parse(line.slice(5).trim()); } catch { continue; }

          if (json.stream_id) {
            streamIdRef.current = json.stream_id;
          }
          if (json.error) {
            fullText = `⚠️ ${json.error}`;
            setAiState("error");
            break;
          }
          if (json.token) {
            fullText += json.token;
            setStreamingText(fullText);
          }
          if (json.stopped) {
            // User stopped — commit whatever was streamed so far
            reader.cancel();
            break;
          }
          if (json.done) break;
        }
      }

      setStreamingText(null);
      setAiState("idle");
      streamIdRef.current = null;
      setMessages(prev => [...prev, { role: "bot", text: fullText }]);
      saveSession(query, fullText);
    } catch {
      setStreamingText(null);
      setAiState("error");
      setMessages(prev => [...prev, { role: "bot", text: "⚠️ Could not reach the API. Is the server running on port 8000?" }]);
      setTimeout(() => setAiState("idle"), 3000);
    }

    setIsStreaming(false);
  }, [input, isStreaming, isRecording, saveSession]);

  const loadSession = (s: Session) => {
    setActiveSession(s.id);
    setMessages([{ role: "user", text: s.query }, { role: "bot", text: s.answer }]);
    setShowWelcome(false);
    setSidebarOpen(false);
  };

  const newChat = () => {
    setMessages([]);
    setShowWelcome(true);
    setActiveSession(null);
    setSidebarOpen(false);
    textareaRef.current?.focus();
  };

  const logout = () => {
    sessionStorage.clear();
    navigate("/");
  };

  if (!storedUser) return null;

  return (
    <div className="h-screen overflow-hidden flex relative">
      {/* Recording notification banner */}
      {isRecording && (
        <div className="fixed top-0 left-0 right-0 z-[300] flex items-center justify-center gap-2 py-2.5 bg-danger/90 text-scb-text text-sm font-medium backdrop-blur-sm" style={{ animation: "fadeUp 0.2s ease both" }}>
          <span className="text-lg">🎤</span>
          Recording... Click stop when finished
        </div>
      )}

      {/* Sidebar */}
      <aside className={`w-[248px] shrink-0 bg-surface border-r border-border flex flex-col p-5 px-3.5 transition-transform z-[100] max-md:fixed max-md:inset-y-0 max-md:left-0 ${sidebarOpen ? "max-md:translate-x-0" : "max-md:-translate-x-full"}`}>
        <div className="flex items-center gap-2.5 px-1.5 pb-4 border-b border-border mb-3.5 text-sm font-semibold text-scb-text">
          <svg width="24" height="24" viewBox="0 0 36 36" fill="none">
            <rect x="1" y="1" width="34" height="34" rx="8" stroke="hsl(var(--accent-color))" strokeWidth="1.5"/>
            <path d="M18 8v20M8 18h20" stroke="hsl(var(--accent-color))" strokeWidth="2" strokeLinecap="round"/>
          </svg>
          SecureCareBot
        </div>

        <button onClick={newChat} className="flex items-center gap-2 w-full py-2.5 px-3 bg-accent-glow border border-accent-dim rounded-lg text-primary text-sm font-medium mb-4 hover:bg-[hsl(var(--accent-glow)/0.18)] transition-colors">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 5v14M5 12h14"/></svg>
          New session
        </button>

        <div className="text-[0.62rem] font-mono text-scb-text-3 uppercase tracking-[0.1em] px-1.5 mb-1.5">RECENT</div>
        <ul className="flex-1 overflow-y-auto flex flex-col gap-0.5 scrollbar-thin">
          {sessions.length === 0 && (
            <li className="text-[0.72rem] font-mono text-scb-text-3 px-2.5 py-1.5">No sessions yet</li>
          )}
          {sessions.map(s => (
            <li
              key={s.id}
              onClick={() => loadSession(s)}
              className={`flex items-center gap-2 py-2 px-2.5 rounded-lg text-[0.8rem] cursor-pointer truncate transition-colors ${activeSession === s.id ? "bg-surface-3 text-primary" : "text-scb-text-2 hover:bg-surface-2 hover:text-scb-text"}`}
            >
              <span className={`w-1 h-1 rounded-full shrink-0 ${activeSession === s.id ? "bg-primary" : "bg-scb-text-3"}`} />
              {s.title}
            </li>
          ))}
        </ul>

        <div className="border-t border-border pt-3.5 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="w-[30px] h-[30px] bg-accent-dim rounded-lg flex items-center justify-center text-[0.78rem] font-semibold text-primary shrink-0 uppercase">
              {storedUser[0]}
            </div>
            <div className="flex flex-col min-w-0">
              <span className="text-[0.78rem] font-medium text-scb-text truncate">{storedUser}</span>
              <span className="text-[0.65rem] font-mono text-scb-text-3 tracking-wider">Clinician</span>
            </div>
          </div>
          <button onClick={logout} className="text-scb-text-3 p-1.5 rounded-md shrink-0 hover:text-danger hover:bg-danger/10 transition-colors" title="Sign out">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>
            </svg>
          </button>
        </div>
      </aside>

      {/* Mobile sidebar toggle */}
      <button
        onClick={() => setSidebarOpen(!sidebarOpen)}
        className="hidden max-md:flex fixed top-3.5 left-3.5 z-[200] bg-surface-2 border border-border w-9 h-9 rounded-lg items-center justify-center text-scb-text-2"
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>
        </svg>
      </button>

      {/* Main chat area */}
      <main className="flex-1 flex flex-col min-w-0 bg-background relative">
        {/* Header */}
        <header className="flex items-center justify-between py-3.5 px-6 border-b border-border bg-background sticky top-0 z-10 max-md:pl-[60px]">
          <div className="flex items-center gap-2.5">
            <span className="w-2 h-2 bg-success rounded-full" style={{ boxShadow: "0 0 0 2px rgba(76,175,125,0.25)", animation: "pulse-dot 2s ease infinite" }} />
            <span className="text-sm font-medium text-scb-text">Patient Query Assistant</span>
          </div>
          <div className="flex items-center gap-3">
            {/* AI State Indicator */}
            {aiState !== "idle" && (
              <span className={`text-[0.7rem] font-mono px-2.5 py-1 rounded-full border transition-all ${aiState === "error" ? "text-danger border-danger/30 bg-danger/10" : "text-primary border-accent-dim bg-accent-glow"}`} style={{ animation: "fadeUp 0.2s ease both" }}>
                {aiState === "thinking" && <span className="inline-block mr-1.5 animate-pulse">◉</span>}
                {aiState === "generating" && <span className="inline-block mr-1.5 animate-pulse">◎</span>}
                {aiState === "checking_sources" && <span className="inline-block mr-1.5 animate-pulse">◈</span>}
                {aiState === "error" && <span className="inline-block mr-1.5">✕</span>}
                {aiStateText(aiState)}
              </span>
            )}
            <span className="font-mono text-[0.65rem] text-primary bg-accent-glow border border-accent-dim py-1 px-2.5 rounded-full tracking-widest">
              phi3.5 · hybrid RAG
            </span>
          </div>
        </header>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-6 flex flex-col max-md:p-4">
          {showWelcome && (
            <div className="flex-1 flex flex-col items-center justify-center text-center gap-4 py-10" style={{ animation: "fadeUp 0.5s ease both" }}>
              <div className="opacity-70">
                <svg width="48" height="48" viewBox="0 0 36 36" fill="none">
                  <rect x="1" y="1" width="34" height="34" rx="10" stroke="hsl(var(--accent-color))" strokeWidth="1.2" opacity="0.6"/>
                  <path d="M18 8v20M8 18h20" stroke="hsl(var(--accent-color))" strokeWidth="1.8" strokeLinecap="round"/>
                  <circle cx="18" cy="18" r="5" fill="hsl(var(--accent-color))" opacity="0.1"/>
                </svg>
              </div>
              <h1 className="text-2xl font-semibold tracking-tight text-scb-text">How can I help?</h1>
              <p className="text-sm text-scb-text-2 max-w-sm leading-relaxed">Ask about patient symptoms, history, diagnoses, medications, lab results, or scan reports.</p>
              <div className="flex flex-wrap gap-2 justify-center mt-2">
                {SUGGESTIONS.map(s => (
                  <button key={s} onClick={() => sendMessage(s)} className="py-2 px-3.5 bg-surface-2 border border-border rounded-full text-[0.78rem] text-scb-text-2 hover:bg-surface-3 hover:border-accent-dim hover:text-scb-text transition-colors">
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Messages list */}
          <div className="flex flex-col gap-6 w-full max-w-[720px] mx-auto">
            {messages.map((msg, i) => (
              <div key={i} className={`flex gap-3 ${msg.role === "user" ? "flex-row-reverse" : ""}`} style={{ animation: "msgIn 0.3s ease both" }}>
                <div className={`w-8 h-8 rounded-[9px] shrink-0 flex items-center justify-center text-[0.72rem] font-semibold uppercase ${msg.role === "user" ? "bg-accent-dim text-primary" : "bg-surface-2 border border-border text-scb-text-2"}`}>
                  {msg.role === "user" ? storedUser[0] : "✦"}
                </div>
                <div className={`flex flex-col gap-1 max-w-[84%] ${msg.role === "user" ? "items-end" : ""}`}>
                  <div className={`py-3 px-4 rounded-xl text-[0.87rem] leading-relaxed break-words ${msg.role === "user" ? "bg-primary text-primary-foreground rounded-br-sm font-medium" : "bg-surface-2 border border-border text-scb-text rounded-bl-sm"}`}>
                    {msg.role === "bot" ? (
                      <div className="prose prose-sm prose-invert max-w-none [&_p]:my-1 [&_ul]:my-1 [&_ol]:my-1 [&_li]:my-0.5 [&_code]:bg-surface-3 [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:rounded [&_code]:text-primary [&_code]:font-mono [&_code]:text-xs [&_pre]:bg-surface-3 [&_pre]:rounded-lg [&_pre]:p-3 [&_pre]:my-2 [&_h1]:text-lg [&_h2]:text-base [&_h3]:text-sm [&_a]:text-primary [&_a]:underline [&_blockquote]:border-l-2 [&_blockquote]:border-accent-dim [&_blockquote]:pl-3 [&_blockquote]:text-scb-text-2 [&_table]:text-xs [&_th]:p-2 [&_td]:p-2 [&_th]:border [&_td]:border [&_th]:border-border [&_td]:border-border">
                        <ReactMarkdown>{msg.text}</ReactMarkdown>
                      </div>
                    ) : msg.text}
                  </div>
                </div>
              </div>
            ))}

            {/* Streaming bubble with markdown */}
            {streamingText !== null && streamingText !== "" && (
              <div className="flex gap-3" style={{ animation: "msgIn 0.3s ease both" }}>
                <div className="w-8 h-8 rounded-[9px] shrink-0 flex items-center justify-center text-[0.72rem] font-semibold bg-surface-2 border border-border text-scb-text-2">✦</div>
                <div className="flex flex-col gap-1 max-w-[84%]">
                  <div className="py-3 px-4 rounded-xl rounded-bl-sm text-[0.87rem] leading-relaxed break-words bg-surface-2 border border-border text-scb-text">
                    <div className="prose prose-sm prose-invert max-w-none [&_p]:my-1 [&_ul]:my-1 [&_ol]:my-1 [&_li]:my-0.5 [&_code]:bg-surface-3 [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:rounded [&_code]:text-primary [&_code]:font-mono [&_code]:text-xs [&_pre]:bg-surface-3 [&_pre]:rounded-lg [&_pre]:p-3 [&_pre]:my-2">
                      <ReactMarkdown>{streamingText}</ReactMarkdown>
                    </div>
                    <span className="inline-block w-0.5 h-3.5 bg-primary ml-0.5 align-middle rounded-sm" style={{ animation: "blink 0.9s step-end infinite" }} />
                  </div>
                </div>
              </div>
            )}

            {/* Typing indicator */}
            {isStreaming && streamingText === "" && (
              <div className="flex gap-3" style={{ animation: "msgIn 0.3s ease both" }}>
                <div className="w-8 h-8 rounded-[9px] shrink-0 flex items-center justify-center text-[0.72rem] font-semibold bg-surface-2 border border-border text-scb-text-2">✦</div>
                <div className="py-3.5 px-4 rounded-xl rounded-bl-sm bg-surface-2 border border-border flex items-center gap-1">
                  {[0, 1, 2].map(i => (
                    <span key={i} className="w-1.5 h-1.5 bg-scb-text-3 rounded-full" style={{ animation: `bounce-dot 1.2s ease infinite ${i * 0.2}s` }} />
                  ))}
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>
        </div>

        {/* Input area */}
        <div className="py-4 px-6 border-t border-border bg-background flex flex-col gap-2 items-center max-md:px-4 max-md:py-3">
          <div className="w-full max-w-[720px] flex items-end gap-2.5 bg-surface border border-border rounded-2xl py-2.5 px-2.5 pl-4 transition-all focus-within:border-accent-dim focus-within:shadow-[0_0_0_3px_hsl(var(--accent-glow)/0.12)]">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={e => { setInput(e.target.value); autoResize(); }}
              onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); } }}
              className="flex-1 bg-transparent border-none outline-none text-scb-text text-sm leading-relaxed resize-none max-h-[160px] overflow-y-auto placeholder:text-scb-text-3"
              placeholder={isRecording ? "Listening..." : "Ask about a patient…"}
              rows={1}
              disabled={isStreaming}
            />

            {/* Voice input button */}
            {isRecognitionReady && (
              <button
                onClick={toggleRecording}
                disabled={isStreaming}
                className={`w-[38px] h-[38px] rounded-lg flex items-center justify-center shrink-0 transition-all disabled:opacity-30 disabled:pointer-events-none ${isRecording ? "bg-danger text-scb-text animate-pulse" : "bg-surface-2 border border-border text-scb-text-2 hover:text-primary hover:border-accent-dim"}`}
                title={isRecording ? "Stop recording" : "Start voice input"}
              >
                {isRecording ? (
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
                ) : (
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
                    <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                    <line x1="12" y1="19" x2="12" y2="23"/>
                    <line x1="8" y1="23" x2="16" y2="23"/>
                  </svg>
                )}
              </button>
            )}

            {isStreaming ? (
              <button
                onClick={stopGeneration}
                className="w-[38px] h-[38px] bg-danger text-scb-text rounded-lg flex items-center justify-center shrink-0 transition-all hover:opacity-90 hover:shadow-[0_4px_14px_rgba(239,68,68,0.35)] hover:-translate-y-px active:translate-y-0"
                title="Stop generation"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                  <rect x="4" y="4" width="16" height="16" rx="2"/>
                </svg>
              </button>
            ) : (
              <button
                onClick={() => sendMessage()}
                disabled={!input.trim()}
                className="w-[38px] h-[38px] bg-primary text-primary-foreground rounded-lg flex items-center justify-center shrink-0 transition-all hover:opacity-90 hover:shadow-[0_4px_14px_hsl(var(--accent-glow)/0.35)] hover:-translate-y-px active:translate-y-0 disabled:opacity-30 disabled:pointer-events-none"
                title="Send"
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
                  <line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>
                </svg>
              </button>
            )}
          </div>
          <p className="text-[0.67rem] font-mono text-scb-text-3 tracking-wider">SecureCareBot may make errors. Always verify with primary records.</p>
        </div>
      </main>

      {/* Mobile overlay */}
      {sidebarOpen && <div className="fixed inset-0 z-[99] md:hidden" onClick={() => setSidebarOpen(false)} />}
    </div>
  );
};

export default Chat;