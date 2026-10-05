// CTRL+K command palette. Navigation is instant; dangerous actions still route
// through their normal confirmation workflow (the palette never bypasses the
// risk/approval pipeline).

import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

interface Command {
  id: string;
  label: string;
  hint?: string;
  run: () => void;
}

interface Props {
  open: boolean;
  onClose: () => void;
  onFocusNetwork?: () => void;
  onPause?: () => void;
}

export default function CommandPalette({ open, onClose, onFocusNetwork, onPause }: Props) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const commands: Command[] = useMemo(
    () => [
      { id: "overview", label: "Open Overview", run: () => navigate("/") },
      { id: "network", label: "Open Network", run: () => navigate("/network") },
      { id: "focus-network", label: "Focus Network", run: () => { navigate("/network"); onFocusNetwork?.(); } },
      { id: "agents", label: "Open Agents", run: () => navigate("/agents") },
      { id: "generations", label: "Open Generations", run: () => navigate("/generations") },
      { id: "research", label: "Open Research", run: () => navigate("/research") },
      { id: "strategies", label: "Open Strategies", run: () => navigate("/strategies") },
      { id: "portfolio", label: "Open Portfolio", run: () => navigate("/portfolio") },
      { id: "approvals", label: "Open Approvals", run: () => navigate("/approvals") },
      { id: "activity", label: "Search Activity", run: () => navigate("/activity") },
      { id: "system", label: "Open System", run: () => navigate("/system") },
      { id: "settings", label: "Open Settings", run: () => navigate("/settings") },
      { id: "pause", label: "Pause System (emergency stop)", hint: "requires confirmation", run: () => { onPause?.(); navigate("/settings"); } },
    ],
    [navigate, onFocusNetwork, onPause]
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter((c) => c.label.toLowerCase().includes(q));
  }, [commands, query]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setIndex(0);
      setTimeout(() => inputRef.current?.focus(), 20);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setIndex((i) => Math.min(filtered.length - 1, i + 1));
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setIndex((i) => Math.max(0, i - 1));
      }
      if (event.key === "Enter") {
        event.preventDefault();
        const command = filtered[index];
        if (command) {
          command.run();
          onClose();
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, filtered, index, onClose]);

  if (!open) return null;

  return (
    <div className="palette-backdrop" onClick={onClose}>
      <div className="palette" onClick={(e) => e.stopPropagation()}>
        <input
          ref={inputRef}
          className="palette-input"
          placeholder="Type a command…"
          value={query}
          onChange={(e) => { setQuery(e.target.value); setIndex(0); }}
        />
        <div className="palette-list">
          {filtered.map((command, i) => (
            <button
              key={command.id}
              className={`palette-item ${i === index ? "active" : ""}`}
              onMouseEnter={() => setIndex(i)}
              onClick={() => { command.run(); onClose(); }}
            >
              <span>{command.label}</span>
              {command.hint && <span className="faint" style={{ fontSize: 11 }}>{command.hint}</span>}
            </button>
          ))}
          {filtered.length === 0 && <div className="empty">No matching command.</div>}
        </div>
        <div className="palette-foot faint">
          <span>↑↓ navigate</span><span>↵ run</span><span>esc close</span>
        </div>
      </div>
    </div>
  );
}
