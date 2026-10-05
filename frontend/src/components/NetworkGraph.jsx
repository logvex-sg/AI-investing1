// The Network page centrepiece: the Overseer and its agents rendered as an
// interactive SVG graph with pan, zoom, selection and generation filtering.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const RELATION_STYLE = {
  COORDINATES: { color: "#38bdf8", dash: "none" },
  DEBATE: { color: "#f59e0b", dash: "6 4" },
  COLLABORATES: { color: "#34d399", dash: "none" },
  INHERITS_FROM: { color: "#8b5cf6", dash: "2 4" },
  COMPETES: { color: "#fb7185", dash: "4 4" },
};

function layout(nodes) {
  const agents = nodes.filter((n) => n.kind === "AGENT");
  const overseer = nodes.find((n) => n.kind === "OVERSEER");
  const positions = {};
  if (overseer) positions[overseer.id] = { x: 0, y: 0 };
  const radius = 260;
  agents.forEach((agent, index) => {
    const angle = (index / Math.max(1, agents.length)) * Math.PI * 2 - Math.PI / 2;
    positions[agent.id] = {
      x: Math.cos(angle) * radius,
      y: Math.sin(angle) * radius,
    };
  });
  return positions;
}

export default function NetworkGraph({ nodes, edges, onSelect, selectedId }) {
  const [view, setView] = useState({ x: 0, y: 0, scale: 1 });
  const [filter, setFilter] = useState("ALL");
  const [showAllEdges, setShowAllEdges] = useState(true);
  const dragging = useRef(null);
  const svgRef = useRef(null);

  const positions = useMemo(() => layout(nodes), [nodes]);

  const nodeById = useMemo(() => {
    const map = {};
    nodes.forEach((n) => {
      map[n.id] = n;
    });
    return map;
  }, [nodes]);

  const visibleEdges = useMemo(() => {
    return edges.filter((edge) => {
      if (edge.relation === "COORDINATES") return true;
      if (showAllEdges) return true;
      return filter !== "ALL" && edge.relation === filter;
    });
  }, [edges, showAllEdges, filter]);

  const onWheel = useCallback((event) => {
    event.preventDefault();
    setView((v) => {
      const next = Math.max(0.35, Math.min(2.4, v.scale * (event.deltaY < 0 ? 1.1 : 0.9)));
      return { ...v, scale: next };
    });
  }, []);

  const onMouseDown = useCallback((event) => {
    dragging.current = { x: event.clientX, y: event.clientY, view };
  }, [view]);

  const onMouseMove = useCallback((event) => {
    if (!dragging.current) return;
    const dx = event.clientX - dragging.current.x;
    const dy = event.clientY - dragging.current.y;
    setView({
      scale: dragging.current.view.scale,
      x: dragging.current.view.x + dx,
      y: dragging.current.view.y + dy,
    });
  }, []);

  const stopDrag = useCallback(() => {
    dragging.current = null;
  }, []);

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return undefined;
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, [onWheel]);

  const relationFilters = useMemo(() => {
    const set = new Set(edges.map((e) => e.relation));
    return ["ALL", ...Array.from(set)];
  }, [edges]);

  return (
    <div className="network-wrap">
      <div className="network-toolbar">
        <span className="faint" style={{ fontSize: 11, letterSpacing: "0.14em" }}>
          RELATION FILTER
        </span>
        {relationFilters.map((relation) => (
          <button
            key={relation}
            className={`btn ${filter === relation ? "" : "ghost"}`}
            style={{ padding: "5px 10px", fontSize: 11 }}
            onClick={() => {
              setFilter(relation);
              setShowAllEdges(relation === "ALL");
            }}
          >
            {relation}
          </button>
        ))}
        <div className="spacer" />
        <button
          className="btn ghost"
          style={{ padding: "5px 10px", fontSize: 11 }}
          onClick={() => setView({ x: 0, y: 0, scale: 1 })}
        >
          Reset view
        </button>
        <span className="faint mono" style={{ fontSize: 11 }}>
          zoom {view.scale.toFixed(2)}×
        </span>
      </div>

      <svg
        ref={svgRef}
        className="network-canvas"
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={stopDrag}
        onMouseLeave={stopDrag}
        viewBox="-480 -400 960 800"
        preserveAspectRatio="xMidYMid meet"
      >
        <defs>
          <radialGradient id="overseerGlow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#38bdf8" stopOpacity="0.9" />
            <stop offset="100%" stopColor="#8b5cf6" stopOpacity="0.1" />
          </radialGradient>
          <filter id="soft" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="6" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        <g transform={`translate(${view.x} ${view.y}) scale(${view.scale})`}>
          {visibleEdges.map((edge, index) => {
            const from = positions[edge.source];
            const to = positions[edge.target];
            if (!from || !to) return null;
            const style = RELATION_STYLE[edge.relation] || RELATION_STYLE.COORDINATES;
            const isOverseerEdge = edge.source === "overseer";
            return (
              <line
                key={`${edge.source}-${edge.target}-${index}`}
                x1={from.x}
                y1={from.y}
                x2={to.x}
                y2={to.y}
                stroke={style.color}
                strokeWidth={isOverseerEdge ? 1 : 0.8 + (edge.weight || 0.5) * 2.4}
                strokeOpacity={isOverseerEdge ? 0.32 : 0.5 + (edge.weight || 0.5) * 0.4}
                strokeDasharray={style.dash === "none" ? undefined : style.dash}
              />
            );
          })}

          {nodes.map((node) => {
            const pos = positions[node.id];
            if (!pos) return null;
            const isOverseer = node.kind === "OVERSEER";
            const selected = selectedId === node.id;
            const r = isOverseer ? 46 : 34;
            return (
              <g
                key={node.id}
                transform={`translate(${pos.x} ${pos.y})`}
                style={{ cursor: "pointer" }}
                onClick={() => onSelect?.(node)}
              >
                {isOverseer && (
                  <circle r={r + 16} fill="url(#overseerGlow)" opacity="0.5" filter="url(#soft)" />
                )}
                <circle
                  r={r}
                  fill={isOverseer ? "rgba(56,189,248,0.22)" : "rgba(30,41,59,0.85)"}
                  stroke={selected ? "#facc15" : isOverseer ? "#38bdf8" : "#475569"}
                  strokeWidth={selected ? 3 : 1.6}
                />
                <text
                  textAnchor="middle"
                  dy={isOverseer ? 4 : -2}
                  fill="#e2e8f0"
                  fontSize={isOverseer ? 13 : 12}
                  fontWeight="700"
                  fontFamily="monospace"
                >
                  {node.label}
                </text>
                {!isOverseer && (
                  <text
                    textAnchor="middle"
                    dy={14}
                    fill="#94a3b8"
                    fontSize={8.5}
                    fontFamily="monospace"
                  >
                    {node.specialization}
                  </text>
                )}
                {!isOverseer && node.score !== undefined && (
                  <text
                    textAnchor="middle"
                    dy={-46}
                    fill={node.score >= 0 ? "#34d399" : "#fb7185"}
                    fontSize={9}
                    fontFamily="monospace"
                  >
                    {Number(node.score).toFixed(3)}
                  </text>
                )}
              </g>
            );
          })}
        </g>
      </svg>

      <div className="network-legend">
        {Object.entries(RELATION_STYLE).map(([relation, style]) => (
          <div className="legend-row" key={relation}>
            <span className="legend-swatch" style={{ background: style.color }} />
            <span>{relation}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
