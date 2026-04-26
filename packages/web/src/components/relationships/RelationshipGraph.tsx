"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import cytoscape, {
  type Core,
  type EdgeSingular,
  type ElementDefinition,
  type EventObject,
  type NodeSingular,
} from "cytoscape";
import fcose from "cytoscape-fcose";
import type { RelationshipEdge, RelationshipGraph as Graph } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

cytoscape.use(fcose);

interface Props {
  graph: Graph;
}

type Strength = RelationshipEdge["strength"];

const EDGE_COLOR: Record<Strength, string> = {
  strong: "#fb7185",
  medium: "#fbbf24",
  weak: "#64748b",
};

const EDGE_WIDTH: Record<Strength, number> = {
  strong: 4,
  medium: 2.5,
  weak: 1.5,
};

const EDGE_OPACITY: Record<Strength, number> = {
  strong: 0.95,
  medium: 0.85,
  weak: 0.55,
};

interface SelectedEdgeState {
  source: number;
  target: number;
  score: number;
  shared_entity_count: number;
  shared_entity_types: Record<string, number>;
  strength: Strength;
  sourceFilename: string;
  targetFilename: string;
}

interface SelectedNodeState {
  id: number;
  filename: string;
  entity_count: number;
  distinct_entity_count: number;
}

function buildElements(graph: Graph): ElementDefinition[] {
  const maxEnt = graph.nodes.reduce(
    (m, n) => Math.max(m, n.distinct_entity_count, n.entity_count),
    1,
  );
  const nodeElements: ElementDefinition[] = graph.nodes.map((n) => {
    const sizeMetric = n.distinct_entity_count || n.entity_count || 0;
    const ratio = Math.sqrt(Math.min(1, sizeMetric / Math.max(1, maxEnt)));
    const radius = 22 + ratio * 32; // 22..54
    return {
      group: "nodes",
      data: {
        id: String(n.id),
        documentId: n.id,
        label: n.filename,
        countLabel: String(n.distinct_entity_count),
        entity_count: n.entity_count,
        distinct_entity_count: n.distinct_entity_count,
        size: radius * 2,
        radius,
      },
    };
  });
  const edgeElements: ElementDefinition[] = graph.edges.map((e) => ({
    group: "edges",
    data: {
      id: `e-${e.source}-${e.target}`,
      source: String(e.source),
      target: String(e.target),
      score: e.score,
      strength: e.strength,
      shared_entity_count: e.shared_entity_count,
      shared_entity_types: e.shared_entity_types,
      color: EDGE_COLOR[e.strength],
      width: EDGE_WIDTH[e.strength],
      opacity: EDGE_OPACITY[e.strength],
    },
  }));
  return [...nodeElements, ...edgeElements];
}

interface FilenameOverlayItem {
  id: string;
  documentId: number;
  text: string;
  x: number;
  y: number;
  offsetY: number;
}

// Filename labels are hidden by default and surface only on hover or
// when the user has zoomed in far enough that the labels won't overlap
// each other. This mirrors how Gephi / Neo4j Bloom / Cytoscape Desktop
// handle dense graphs — a wall of overlapping filenames carries no
// information, while hover-on-demand keeps the count-only view clean.
const LABEL_VISIBILITY_ZOOM_THRESHOLD = 1.6;

export default function RelationshipGraph({ graph }: Props) {
  const t = useI18n();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<SelectedEdgeState | null>(null);
  const [hoveredNode, setHoveredNode] = useState<SelectedNodeState | null>(null);
  const [filenameOverlays, setFilenameOverlays] = useState<FilenameOverlayItem[]>(
    [],
  );
  const [overlayZoom, setOverlayZoom] = useState(1);

  const elements = useMemo(() => buildElements(graph), [graph]);
  const nodeFilenameById = useMemo(() => {
    const map = new Map<number, string>();
    for (const n of graph.nodes) map.set(n.id, n.filename);
    return map;
  }, [graph]);

  useEffect(() => {
    if (!containerRef.current) return;
    if (graph.nodes.length === 0) return;

    const cy = cytoscape({
      container: containerRef.current,
      elements,
      wheelSensitivity: 0.25,
      minZoom: 0.1,
      maxZoom: 4,
      style: [
        {
          selector: "node",
          style: {
            "background-color": "#1e293b",
            "border-color": "#475569",
            "border-width": 1.5,
            // The in-circle label is the distinct-entity count — the
            // only piece of text that scales sensibly inside a 30–60px
            // circle. Filenames are rendered in a separate React DOM
            // overlay layer because cytoscape supports only one label
            // per node, and stacking count + long filename in a single
            // ``text-wrap`` label crowds out small nodes.
            label: "data(countLabel)",
            color: "#f8fafc",
            "font-size": 13,
            "font-weight": 600,
            "font-family": "system-ui, -apple-system, sans-serif",
            "text-valign": "center",
            "text-halign": "center",
            width: "data(size)",
            height: "data(size)",
          },
        },
        {
          selector: "node.faded",
          style: {
            opacity: 0.18,
          },
        },
        {
          selector: "node.highlighted",
          style: {
            "background-color": "#0ea5e9",
            "border-color": "#7dd3fc",
            "border-width": 3,
            "z-index": 10,
          },
        },
        {
          selector: "edge",
          style: {
            "curve-style": "bezier",
            "line-color": "data(color)",
            width: "data(width)",
            opacity: "data(opacity)",
            "target-arrow-shape": "none",
          },
        },
        {
          selector: "edge.faded",
          style: {
            opacity: 0.05,
          },
        },
        {
          selector: "edge.highlighted",
          style: {
            opacity: 1,
            width: (ele: EdgeSingular) =>
              (ele.data("width") as number) * 1.6,
            "z-index": 10,
          },
        },
      ],
      layout: {
        name: "fcose",
        animate: false,
        randomize: true,
        // fcose's "componentSpacing" + nodeRepulsion are what give the
        // visual cluster separation the user asked for: connected sets
        // collapse into their own gravity well, isolated docs stay
        // clearly apart in their own corner.
        nodeRepulsion: () => 12_000,
        idealEdgeLength: () => 110,
        edgeElasticity: () => 0.45,
        gravity: 0.25,
        gravityRangeCompound: 1.5,
        // Push tighter clusters apart so the eye reads them as
        // distinct groups rather than one big tangle.
        // @ts-expect-error fcose-specific options not in cytoscape types
        componentSpacing: 80,
        // @ts-expect-error fcose-specific
        nodeDimensionsIncludeLabels: true,
        // @ts-expect-error fcose-specific
        packComponents: true,
        padding: 30,
        fit: true,
        numIter: 2500,
      } as cytoscape.LayoutOptions,
    });

    cyRef.current = cy;

    const applyHighlight = (kind: "edge" | "node", ele: EdgeSingular | NodeSingular) => {
      cy.elements().removeClass("highlighted faded");
      if (kind === "edge") {
        const edge = ele as EdgeSingular;
        const endpoints = edge.connectedNodes();
        edge.addClass("highlighted");
        endpoints.addClass("highlighted");
        cy.elements()
          .difference(endpoints.union(edge))
          .addClass("faded");
      } else {
        const node = ele as NodeSingular;
        const incidentEdges = node.connectedEdges();
        const neighbours = node.neighborhood().nodes().union(node);
        incidentEdges.addClass("highlighted");
        node.addClass("highlighted");
        cy.elements()
          .difference(neighbours.union(incidentEdges))
          .addClass("faded");
      }
    };

    const clearHighlight = () => {
      cy.elements().removeClass("highlighted faded");
    };

    cy.on("tap", "edge", (event: EventObject) => {
      const edge = event.target as EdgeSingular;
      const sourceId = Number(edge.source().id());
      const targetId = Number(edge.target().id());
      setSelectedEdge({
        source: sourceId,
        target: targetId,
        score: edge.data("score") as number,
        strength: edge.data("strength") as Strength,
        shared_entity_count: edge.data("shared_entity_count") as number,
        shared_entity_types: edge.data("shared_entity_types") as Record<
          string,
          number
        >,
        sourceFilename: nodeFilenameById.get(sourceId) ?? String(sourceId),
        targetFilename: nodeFilenameById.get(targetId) ?? String(targetId),
      });
      applyHighlight("edge", edge);
    });

    cy.on("mouseover", "node", (event: EventObject) => {
      const node = event.target as NodeSingular;
      setHoveredNode({
        id: Number(node.id()),
        filename: node.data("label") as string,
        entity_count: node.data("entity_count") as number,
        distinct_entity_count: node.data("distinct_entity_count") as number,
      });
      if (!selectedEdge) applyHighlight("node", node);
    });
    cy.on("mouseout", "node", () => {
      setHoveredNode(null);
      if (!selectedEdge) clearHighlight();
    });
    cy.on("tap", (event: EventObject) => {
      // Clicking blank canvas clears selection.
      if (event.target === cy) {
        setSelectedEdge(null);
        clearHighlight();
      }
    });

    // Mirror node positions into a React-rendered overlay so filenames
    // can render BELOW the cytoscape node (cytoscape only supports one
    // label per node, and we use that single label for the in-circle
    // count). Synced on layout, render, pan and zoom so the labels
    // track the camera at all times.
    const syncFilenameOverlays = () => {
      const z = cy.zoom();
      const items: FilenameOverlayItem[] = cy.nodes().map((node) => ({
        id: node.id(),
        documentId: Number(node.id()),
        text: node.data("label") as string,
        x: node.renderedPosition().x,
        y: node.renderedPosition().y,
        offsetY: ((node.data("radius") as number) ?? 24) * z + 6,
      }));
      setFilenameOverlays(items);
      setOverlayZoom(z);
    };
    cy.on("layoutstop render pan zoom", syncFilenameOverlays);
    syncFilenameOverlays();

    return () => {
      cy.destroy();
      cyRef.current = null;
    };
    // We intentionally rebuild the cytoscape instance whenever the graph
    // identity changes — fcose runs once per mount and the layout cost
    // is acceptable for the corpus sizes we expect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [elements]);

  if (graph.nodes.length === 0) {
    return (
      <div className="rounded-lg border border-slate-800 bg-slate-950/40 p-8 text-center text-sm text-slate-400">
        {t("relationships.empty")}
      </div>
    );
  }

  const handleFit = () => cyRef.current?.fit(undefined, 40);
  const handleZoomIn = () => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.zoom({ level: cy.zoom() * 1.25, position: cy.pan() });
  };
  const handleZoomOut = () => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.zoom({ level: cy.zoom() / 1.25, position: cy.pan() });
  };

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr,320px]">
      <div className="relative overflow-hidden rounded-lg border border-slate-800 bg-slate-950/60">
        <div className="absolute right-3 top-3 z-10 flex flex-col gap-1">
          <button
            type="button"
            onClick={handleZoomIn}
            className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-xs font-medium text-slate-100 hover:bg-slate-800"
          >
            +
          </button>
          <button
            type="button"
            onClick={handleZoomOut}
            className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-xs font-medium text-slate-100 hover:bg-slate-800"
          >
            −
          </button>
          <button
            type="button"
            onClick={handleFit}
            className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-[10px] font-medium text-slate-200 hover:bg-slate-800"
            title={t("relationships.fitView")}
          >
            ⤢
          </button>
        </div>

        <div
          ref={containerRef}
          className="block h-[640px] w-full"
          aria-label={t("relationships.svgAriaLabel")}
        />

        {/* Filename overlays — positioned manually because cytoscape
            only supports one label per node and we use that label for
            the in-circle count. Visibility is gated on zoom so the
            default overview stays clean (count-only); zooming past the
            threshold or hovering an individual node surfaces names on
            demand. The overlay stays in sync with the cytoscape camera
            via the ``layoutstop render pan zoom`` event handler. */}
        <div className="pointer-events-none absolute inset-0 overflow-hidden">
          {filenameOverlays.map((item) => {
            const isHovered = hoveredNode?.id === item.documentId;
            const isSelectedEndpoint =
              selectedEdge != null &&
              (selectedEdge.source === item.documentId ||
                selectedEdge.target === item.documentId);
            const showByZoom = overlayZoom >= LABEL_VISIBILITY_ZOOM_THRESHOLD;
            if (!showByZoom && !isHovered && !isSelectedEndpoint) return null;
            return (
              <div
                key={item.id}
                className={`absolute -translate-x-1/2 whitespace-nowrap text-[11px] font-medium ${
                  isHovered || isSelectedEndpoint
                    ? "text-sky-200"
                    : "text-slate-300"
                }`}
                style={{
                  left: item.x,
                  top: item.y + item.offsetY,
                  textShadow: "0 0 3px #020617, 0 0 3px #020617",
                }}
              >
                {item.text.length > 30 ? `${item.text.slice(0, 28)}…` : item.text}
              </div>
            );
          })}
        </div>

        <div className="pointer-events-none absolute bottom-2 left-3 text-[10px] text-slate-500">
          {t("relationships.zoomHint")}
        </div>
      </div>

      <aside className="rounded-lg border border-slate-800 bg-slate-950/60 p-4 text-sm">
        <h2 className="text-base font-semibold text-slate-100">
          {t("relationships.legend.title")}
        </h2>
        <ul className="mt-3 space-y-2 text-xs text-slate-300">
          <li className="flex items-center gap-2">
            <span
              className="inline-block h-1 w-8 rounded-full"
              style={{ background: EDGE_COLOR.strong }}
            />
            {t("relationships.legend.strong")}
          </li>
          <li className="flex items-center gap-2">
            <span
              className="inline-block h-1 w-8 rounded-full"
              style={{ background: EDGE_COLOR.medium }}
            />
            {t("relationships.legend.medium")}
          </li>
          <li className="flex items-center gap-2">
            <span
              className="inline-block h-1 w-8 rounded-full"
              style={{ background: EDGE_COLOR.weak }}
            />
            {t("relationships.legend.weak")}
          </li>
        </ul>

        <div className="mt-6 border-t border-slate-800 pt-4">
          {selectedEdge ? (
            <ActiveEdgePanel edge={selectedEdge} />
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

function ActiveEdgePanel({ edge }: { edge: SelectedEdgeState }) {
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
        <div className="mt-1 break-all text-sm font-semibold text-slate-100">
          {edge.sourceFilename} ↔ {edge.targetFilename}
        </div>
      </div>
      <div className="flex items-center gap-4 text-xs">
        <div>
          <div className="text-slate-500">
            {t("relationships.edgePanel.score")}
          </div>
          <div className="text-slate-100">{edge.score.toFixed(2)}</div>
        </div>
        <div>
          <div className="text-slate-500">
            {t("relationships.edgePanel.strength")}
          </div>
          <div className="text-slate-100 capitalize">{edge.strength}</div>
        </div>
        <div>
          <div className="text-slate-500">
            {t("relationships.edgePanel.sharedCount")}
          </div>
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

function NodePanel({ node }: { node: SelectedNodeState }) {
  const t = useI18n();
  return (
    <div className="space-y-2">
      <div className="text-xs uppercase tracking-wide text-slate-500">
        {t("relationships.nodePanel.document")}
      </div>
      <div className="break-all text-sm font-semibold text-slate-100">
        {node.filename}
      </div>
      <div className="flex gap-4 text-xs text-slate-300">
        <div>
          <div className="text-slate-500">
            {t("relationships.nodePanel.distinctEntities")}
          </div>
          <div className="text-slate-100">{node.distinct_entity_count}</div>
        </div>
        <div>
          <div className="text-slate-500">
            {t("relationships.nodePanel.totalDetections")}
          </div>
          <div className="text-slate-100">{node.entity_count}</div>
        </div>
      </div>
    </div>
  );
}
