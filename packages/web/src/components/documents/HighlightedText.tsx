"use client";

import { useMemo } from "react";
import type { EntityDetection } from "@/lib/types";
import {
  getEntityFilledClasses,
  getEntityOutlineClasses,
} from "@/lib/entityColors";

interface HighlightedTextProps {
  text: string;
  detections: EntityDetection[];
  activeEntityType?: string | null;
  activeDetectionId?: number | null;
}

interface Segment {
  text: string;
  detection?: EntityDetection;
}

function buildSegments(
  text: string,
  detections: EntityDetection[]
): Segment[] {
  if (detections.length === 0) {
    return [{ text }];
  }

  const sorted = [...detections].sort((a, b) => a.start_offset - b.start_offset);
  const segments: Segment[] = [];
  let cursor = 0;

  for (const det of sorted) {
    if (det.start_offset < cursor || det.start_offset >= text.length) {
      continue;
    }
    if (det.start_offset > cursor) {
      segments.push({ text: text.slice(cursor, det.start_offset) });
    }
    const end = Math.min(det.end_offset, text.length);
    segments.push({ text: text.slice(det.start_offset, end), detection: det });
    cursor = end;
  }

  if (cursor < text.length) {
    segments.push({ text: text.slice(cursor) });
  }

  return segments;
}

export function HighlightedText({
  text,
  detections,
  activeEntityType,
  activeDetectionId,
}: HighlightedTextProps) {
  const segments = useMemo(() => buildSegments(text, detections), [text, detections]);

  return (
    <>
      {segments.map((seg, i) => {
        if (!seg.detection) {
          return <span key={i}>{seg.text}</span>;
        }

        const isFocused = activeDetectionId === seg.detection.id;
        const isActive =
          !activeEntityType || seg.detection.entity_type === activeEntityType;

        let highlightClass: string;
        if (!isActive) {
          highlightClass = "opacity-30";
        } else if (isFocused) {
          highlightClass = getEntityFilledClasses(seg.detection.entity_type);
        } else {
          highlightClass = getEntityOutlineClasses(seg.detection.entity_type);
        }

        return (
          <span
            key={i}
            data-detection-id={seg.detection.id}
            className={`rounded-sm px-0.5 cursor-default transition-colors ${highlightClass}`}
            title={`${seg.detection.placeholder} (${Math.round(seg.detection.score * 100)}%)`}
          >
            {seg.text}
          </span>
        );
      })}
    </>
  );
}
