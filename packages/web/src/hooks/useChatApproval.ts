import { useCallback, useRef, useState } from "react";
import { approvalApprove, approvalReject } from "@/lib/api";
import type { ApprovalChunkPayload, ApprovalData, ChatMessage } from "@/lib/types";
import { useI18n } from "@/lib/i18n";

export interface UseChatApprovalReturn {
  approvalOpen: boolean;
  approvalSessionId: string | null;
  approvalMaskedPrompt: string;
  approvalAssembledPrompt: string;
  approvalChunks: ApprovalChunkPayload[];
  approvalRegulations: string[];
  handleApprove: (
    sessionId: string,
    editedChunks: ApprovalChunkPayload[]
  ) => Promise<void>;
  handleReject: (sessionId: string, reason?: string) => void;
  onApprovalRequired: (
    sessionId: string,
    maskedPrompt: string,
    chunks: ApprovalChunkPayload[],
    activeRegulations: string[],
    assembledPrompt: string
  ) => void;
  onApprovalRejected: (reason: string) => void;
  closeApprovalModal: () => void;
  lastApprovalDataRef: React.RefObject<ApprovalData | null>;
}

export interface UseChatApprovalOptions {
  stopStreaming: () => void;
  messages: ChatMessage[];
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>;
  onRejectedPersist?: (userText: string, approvalData: ApprovalData) => void;
}

export function useChatApproval({
  stopStreaming,
  messages,
  setMessages,
  onRejectedPersist,
}: UseChatApprovalOptions): UseChatApprovalReturn {
  const t = useI18n();
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [approvalSessionId, setApprovalSessionId] = useState<string | null>(null);
  const [approvalMaskedPrompt, setApprovalMaskedPrompt] = useState("");
  const [approvalAssembledPrompt, setApprovalAssembledPrompt] = useState("");
  const [approvalChunks, setApprovalChunks] = useState<ApprovalChunkPayload[]>([]);
  const [approvalRegulations, setApprovalRegulations] = useState<string[]>([]);

  const maskedPromptRef = useRef("");
  const assembledPromptRef = useRef("");
  const chunksRef = useRef<ApprovalChunkPayload[]>([]);
  const regulationsRef = useRef<string[]>([]);
  const lastApprovalDataRef = useRef<ApprovalData | null>(null);

  const handleApprove = useCallback(
    async (sessionId: string, editedChunks: ApprovalChunkPayload[]) => {
      const data: ApprovalData = {
        decision: "approved",
        masked_prompt: maskedPromptRef.current,
        assembled_prompt: assembledPromptRef.current,
        chunks: editedChunks,
        regulations: regulationsRef.current,
      };
      lastApprovalDataRef.current = data;
      await approvalApprove(sessionId, editedChunks);
      setMessages((prev) => {
        const lastUser = [...prev].reverse().find((m) => m.role === "user");
        if (!lastUser) return prev;
        return prev.map((m) =>
          m.id === lastUser.id ? { ...m, approvalData: data } : m
        );
      });
      setApprovalOpen(false);
      setApprovalSessionId(null);
      setApprovalChunks([]);
      setApprovalMaskedPrompt("");
    },
    [setMessages]
  );

  const applyRejection = useCallback(() => {
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    const userText = lastUser?.content ?? "";

    const data: ApprovalData = {
      decision: "rejected",
      masked_prompt: maskedPromptRef.current,
      assembled_prompt: assembledPromptRef.current,
      chunks: chunksRef.current,
      regulations: regulationsRef.current,
      original_user_message: userText,
    };

    setApprovalOpen(false);
    setApprovalSessionId(null);
    stopStreaming();

    setMessages((prev) => {
      const withoutEmptyAssistant = prev.filter((m, i) => {
        if (i === prev.length - 1 && m.role === "assistant" && m.content === "") return false;
        return true;
      });
      return withoutEmptyAssistant.map((m) =>
        m.id === lastUser?.id ? { ...m, approvalData: data } : m
      );
    });

    setApprovalChunks([]);
    setApprovalMaskedPrompt("");

    if (onRejectedPersist && userText) {
      onRejectedPersist(userText, data);
    }
  }, [messages, stopStreaming, setMessages, onRejectedPersist]);

  const handleReject = useCallback(
    (sessionId: string, reason?: string) => {
      approvalReject(sessionId, reason).catch(() => {});
      applyRejection();
    },
    [applyRejection]
  );

  const onApprovalRequired = useCallback(
    (
      sessionId: string,
      maskedPrompt: string,
      chunks: ApprovalChunkPayload[],
      activeRegulations: string[],
      assembledPrompt: string
    ) => {
      maskedPromptRef.current = maskedPrompt;
      assembledPromptRef.current = assembledPrompt;
      chunksRef.current = chunks;
      regulationsRef.current = activeRegulations;
      setApprovalSessionId(sessionId);
      setApprovalMaskedPrompt(maskedPrompt);
      setApprovalAssembledPrompt(assembledPrompt);
      setApprovalChunks(chunks);
      setApprovalRegulations(activeRegulations);
      setApprovalOpen(true);
    },
    []
  );

  const onApprovalRejected = useCallback(
    (_reason: string) => {
      // Backend-initiated rejection (e.g. timeout): the gate already
      // auto-rejected the session, so do NOT call approvalReject() — the
      // session id is gone and the call would 404. We still want the
      // same UX as a manual reject: mark the user's message as rejected,
      // persist it to the session so the user can resend, and close the
      // modal. ``applyRejection`` is the shared helper that handleReject
      // also uses.
      applyRejection();
    },
    [applyRejection]
  );

  const closeApprovalModal = useCallback(() => {
    setApprovalOpen(false);
    setApprovalSessionId(null);
  }, []);

  return {
    approvalOpen,
    approvalSessionId,
    approvalMaskedPrompt,
    approvalAssembledPrompt,
    approvalChunks,
    approvalRegulations,
    handleApprove,
    handleReject,
    onApprovalRequired,
    onApprovalRejected,
    closeApprovalModal,
    lastApprovalDataRef,
  };
}
