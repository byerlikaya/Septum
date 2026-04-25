"use client";

import { useMemo, useState } from "react";
import type { RelationshipEdge, RelationshipGraph as Graph } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

interface Props {
  graph: Graph;
}

interface NodePosition {
  id: number;
  filename: string;
  entity_count: number;
  distinct_entity_count: number;
  x: number;
  y: number;
  radius: number;
}

const STRENGTH_STYLE: Record<RelationshipEdge["strength"], { stroke: string; opacity: number; width: (score: number) => number }> = {
  strong: {
    stroke: "#fb7185", // rose-400
    opacity: 0.95,
    width: (score) => Math.min(6, 2 + score * 1.2),
  },
  medium: {
    stroke: "#fbbf24", // amber-400
    opacity: 0.8,
    width: (score) => Math.min(4, 1.5 + score * 1.0),
  },
  weak: {
    stroke: "#64748b", // slate-500
    opacity: 0.55,
    width: () => 1.2,
  },
};

const VIEW_W = 900;
const VIEW_H = 620;
const PADDING = 80;
const NODE_MIN_R = 18;
const NODE_MAX_R = 38;

function layoutCircular(nodes: Graph["nodes"]): NodePosition[] {
  if (!nodes.length) return [];
  const cx = VIEW_W / 2;
  const cy = VIEW_H / 2;
  const radius = Math.min(VIEW_W, VIEW_H) / 2 - PADDING;
  const maxEnt = nodes.reduce(
    (m, n) => Math.max(m, n.distinct_entity_count, n.entity_count),
    1,
  );
  return nodes.map((n, i) => {
    const angle = (i / nodes.length) * 2 * Math.PI - Math.PI / 2;
    const sizeMetric = n.distinct_entity_count || n.entity_count || 0;
    const ratio = Math.sqrt(Math.min(1, sizeMetric / Math.max(1, maxEnt)));
    return {
      ...n,
      x: cx + Math.cos(angle) * radius,
      y: cy + Math.sin(angle) * radius,
      radius: NODE_MIN_R + (NODE_MAX_R - NODE_MIN_R) * ratio,
    };
  });
}

export default function RelationshipGraph({ graph }: Props) {
  const t = useI18n();
  const [hoveredEdge, setHoveredEdge] = useState<RelationshipEdge | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<RelationshipEdge | null>(null);
  const [hoveredNode, setHoveredNode] = useState<NodePosition | null>(null);

  const positions = useMemo(() => layoutCircular(graph.nodes), [graph.nodes]);
  const positionsById = useMemo(() => {
    const map = new Map<number, NodePosition>();
    positions.forEach((p) => map.set(p.id, p));
    return map;
  }, [positions]);

  if (!graph.nodes.length) {
    return (
      <div className="rounded-lg border border-slate-800 bg-slate-950/40 p-8 text-center text-sm text-slate-400">
        {t("relationships.empty")}
      </div>
    );
  }

  const activeEdge = selectedEdge ?? hoveredEdge;

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr,320px]">
      <div className="overflow-hidden rounded-lg border border-slate-800 bg-slate-950/60">
        <svg
          viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
          className="block h-full w-full"
          role="img"
          aria-label={t("relationships.svgAriaLabel")}
        >
          {graph.edges.map((edge) => {
            const a = positionsById.get(edge.source);
            const b = positionsById.get(edge.target);
            if (!a || !b) return null;
            const style = STRENGTH_STYLE[edge.strength];
            const isActive =
              activeEdge?.source === edge.source &&
              activeEdge?.target === edge.target;
            return (
              <line
                key={`${edge.source}-${edge.target}`}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke={style.stroke}
                strokeWidth={style.width(edge.score) * (isActive ? 1.6 : 1)}
                strokeLinecap="round"
                opacity={isActive ? 1 : style.opacity}
                onMouseEnter={() => setHoveredEdge(edge)}
                onMouseLeave={() => setHoveredEdge(null)}
                onClick={() =>
                  setSelectedEdge((prev) =>
                    prev?.source === edge.source && prev?.target === edge.target
                      ? null
                      : edge,
                  )
                }
                style={{ cursor: "pointer" }}
              />
            );
          })}

          {positions.map((p) => {
            const isHovered = hoveredNode?.id === p.id;
            const involvedInActive =
              activeEdge &&
              (activeEdge.source === p.id || activeEdge.target === p.id);
            return (
              <g
                key={p.id}
                onMouseEnter={() => setHoveredNode(p)}
                onMouseLeave={() => setHoveredNode(null)}
                style={{ cursor: "pointer" }}
              >
                <circle
                  cx={p.x}
                  cy={p.y}
                  r={p.radius}
                  fill={involvedInActive ? "#0ea5e9" : "#1e293b"}
                  stroke={involvedInActive ? "#7dd3fc" : "#475569"}
                  strokeWidth={isHovered ? 3 : 1.5}
                />
                <text
                  x={p.x}
                  y={p.y + p.radius + 16}
                  textAnchor="middle"
                  className="fill-slate-200 text-xs font-medium"
                  style={{ pointerEvents: "none" }}
                >
                  {p.filename.length > 28
                    ? `${p.filename.slice(0, 26)}…`
                    : p.filename}
                </text>
                <text
                  x={p.x}
                  y={p.y + 4}
                  textAnchor="middle"
                  className="fill-slate-50 text-xs font-semibold"
                  style={{ pointerEvents: "none" }}
                >
                  {p.distinct_entity_count}
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      <aside className="rounded-lg border border-slate-800 bg-slate-950/60 p-4 text-sm">
        <h2 className="text-base font-semibold text-slate-100">
          {t("relationships.legend.title")}
        </h2>
        <ul className="mt-3 space-y-2 text-xs text-slate-300">
          <li className="flex items-center gap-2">
            <span className="inline-block h-1 w-8 rounded-full" style={{ background: STRENGTH_STYLE.strong.stroke }} />
            {t("relationships.legend.strong")}
          </li>
          <li className="flex items-center gap-2">
            <span className="inline-block h-1 w-8 rounded-full" style={{ background: STRENGTH_STYLE.medium.stroke }} />
            {t("relationships.legend.medium")}
          </li>
          <li className="flex items-center gap-2">
            <span className="inline-block h-1 w-8 rounded-full" style={{ background: STRENGTH_STYLE.weak.stroke }} />
            {t("relationships.legend.weak")}
          </li>
        </ul>

        <div className="mt-6 border-t border-slate-800 pt-4">
          {activeEdge ? (
            <ActiveEdgePanel
              edge={activeEdge}
              source={positionsById.get(activeEdge.source)}
              target={positionsById.get(activeEdge.target)}
            />
          ) : hoveredNode ? (
            <NodePanel node={hoveredNode} />
          ) : (
            <p className="text-xs text-slate-500">
              {t("relationships.selectionHint")}
            </p>
          )}
        </div>
      </aside>
    </div>
  );
}

function ActiveEdgePanel({
  edge,
  source,
  target,
}: {
  edge: RelationshipEdge;
  source?: NodePosition;
  target?: NodePosition;
}) {
  const t = useI18n();
  const sortedTypes = Object.entries(edge.shared_entity_types).sort(
    (a, b) => b[1] - a[1],
  );
  return (
    <div className="space-y-3">
      <div>
        <div className="text-xs uppercase tracking-wide text-slate-500">
          {t("relationships.edgePanel.connection")}
        </div>
        <div className="mt-1 text-sm font-semibold text-slate-100">
          {source?.filename ?? edge.source} ↔ {target?.filename ?? edge.target}
        </div>
      </div>
      <div className="flex items-center gap-4 text-xs">
        <div>
          <div className="text-slate-500">{t("relationships.edgePanel.score")}</div>
          <div className="text-slate-100">{edge.score.toFixed(2)}</div>
        </div>
        <div>
          <div className="text-slate-500">{t("relationships.edgePanel.strength")}</div>
          <div className="text-slate-100 capitalize">{edge.strength}</div>
        </div>
        <div>
          <div className="text-slate-500">{t("relationships.edgePanel.sharedCount")}</div>
          <div className="text-slate-100">{edge.shared_entity_count}</div>
        </div>
      </div>
      {sortedTypes.length > 0 && (
        <div>
          <div className="text-xs uppercase tracking-wide text-slate-500">
            {t("relationships.edgePanel.sharedTypes")}
          </div>
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {sortedTypes.map(([type, count]) => (
              <li
                key={type}
                className="rounded-full border border-slate-700 bg-slate-900 px-2 py-0.5 text-xs text-slate-200"
              >
                {type} <span className="text-slate-400">×{count}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function NodePanel({ node }: { node: NodePosition }) {
  const t = useI18n();
  return (
    <div className="space-y-2">
      <div className="text-xs uppercase tracking-wide text-slate-500">
        {t("relationships.nodePanel.document")}
      </div>
      <div className="text-sm font-semibold text-slate-100">{node.filename}</div>
      <div className="flex gap-4 text-xs text-slate-300">
        <div>
          <div className="text-slate-500">{t("relationships.nodePanel.distinctEntities")}</div>
          <div className="text-slate-100">{node.distinct_entity_count}</div>
        </div>
        <div>
          <div className="text-slate-500">{t("relationships.nodePanel.totalDetections")}</div>
          <div className="text-slate-100">{node.entity_count}</div>
        </div>
      </div>
    </div>
  );
}
