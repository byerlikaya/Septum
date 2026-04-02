import { useCallback, useState } from "react";
import { approvalApprove, approvalReject } from "@/lib/api";
import type { ApprovalChunkPayload } from "@/lib/types";
import { useI18n } from "@/lib/i18n";
import type { ChatMessage } from "@/lib/types";

export interface UseChatApprovalReturn {
  approvalOpen: boolean;
  approvalSessionId: string | null;
  approvalMaskedPrompt: string;
  approvalChunks: ApprovalChunkPayload[];
  approvalRegulations: string[];
  approvalTimedOut: boolean;
  handleApprove: (
    sessionId: string,
    editedChunks: ApprovalChunkPayload[]
  ) => Promise<void>;
  handleReject: (
    sessionId: string,
    reason?: string
  ) => Promise<void>;
  onApprovalRequired: (
    sessionId: string,
    maskedPrompt: string,
    chunks: ApprovalChunkPayload[],
    activeRegulations: string[]
  ) => void;
  onApprovalRejected: (reason: string, timedOut: boolean) => void;
  closeApprovalModal: () => void;
}

export interface UseChatApprovalOptions {
  stopStreaming: () => void;
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>;
}

export function useChatApproval({
  stopStreaming,
  setMessages
}: UseChatApprovalOptions): UseChatApprovalReturn {
  const t = useI18n();
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [approvalSessionId, setApprovalSessionId] = useState<string | null>(
    null
  );
  const [approvalMaskedPrompt, setApprovalMaskedPrompt] = useState("");
  const [approvalChunks, setApprovalChunks] = useState<
    ApprovalChunkPayload[]
  >([]);
  const [approvalRegulations, setApprovalRegulations] = useState<string[]>([]);
  const [approvalTimedOut, setApprovalTimedOut] = useState(false);

  const handleApprove = useCallback(
    async (sessionId: string, editedChunks: ApprovalChunkPayload[]) => {
      await approvalApprove(sessionId, editedChunks);
      setApprovalOpen(false);
      setApprovalSessionId(null);
      setApprovalChunks([]);
      setApprovalMaskedPrompt("");
    },
    []
  );

  const handleReject = useCallback(
    async (sessionId: string, reason?: string) => {
      await approvalReject(sessionId, reason);
      setApprovalOpen(false);
      setApprovalSessionId(null);
      setApprovalChunks([]);
      setApprovalMaskedPrompt("");
      stopStreaming();
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (last?.role === "assistant" && last.content === "") {
          return prev.slice(0, -1).concat({
            ...last,
            content: t("chat.approval.rejected")
          });
        }
        return prev;
      });
    },
    [stopStreaming, setMessages, t]
  );

  const onApprovalRequired = useCallback(
    (
      sessionId: string,
      maskedPrompt: string,
      chunks: ApprovalChunkPayload[],
      activeRegulations: string[]
    ) => {
      setApprovalSessionId(sessionId);
      setApprovalMaskedPrompt(maskedPrompt);
      setApprovalChunks(chunks);
      setApprovalRegulations(activeRegulations);
      setApprovalOpen(true);
      setApprovalTimedOut(false);
    },
    []
  );

  const onApprovalRejected = useCallback(
    (_reason: string, timedOut: boolean) => {
      setApprovalTimedOut(timedOut);
      setApprovalOpen(false);
      setApprovalSessionId(null);
    },
    []
  );

  const closeApprovalModal = useCallback(() => {
    setApprovalOpen(false);
    setApprovalSessionId(null);
  }, []);

  return {
    approvalOpen,
    approvalSessionId,
    approvalMaskedPrompt,
    approvalChunks,
    approvalRegulations,
    approvalTimedOut,
    handleApprove,
    handleReject,
    onApprovalRequired,
    onApprovalRejected,
    closeApprovalModal
  };
}
