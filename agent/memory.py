"""
Conversation Memory Module

Manages conversation history per session with context window management
and automatic summary compression for LLM agent interactions.

Usage:
    memory = ConversationMemory()
    memory.add_turn("session_1", "user", "Show revenue by region")
    memory.add_turn("session_1", "assistant", "The revenue by region is...")
    context = memory.get_context("session_1")  # Returns messages for LLM
"""

import json
import logging
import time
from typing import Dict, List, Optional, Any
from collections import OrderedDict

logger = logging.getLogger(__name__)


class ConversationMemory:
    """
    Manages conversation history per session with context window management.

    Maintains a sliding window of messages per session. When the token budget
    is exceeded, old turns are compressed into a running summary via LLM.
    """

    def __init__(self, max_turns: int = 50, summary_callback=None):
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._max_turns = max_turns
        # Optional async callback for summarization: async def summarize(session_id, turns)
        self.summary_callback = summary_callback

    def create_session(self, session_id: str) -> Dict[str, Any]:
        """Create a new conversation session."""
        session = {
            "session_id": session_id,
            "turns": [],
            "summary": "",
            "created_at": time.time(),
            "updated_at": time.time(),
            "turn_count": 0,
        }
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get a session by ID, creating it if it doesn't exist."""
        if session_id not in self._sessions:
            return self.create_session(session_id)
        return self._sessions[session_id]

    def add_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_calls: Optional[List[Dict]] = None,
        tool_results: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Add a conversation turn to a session."""
        session = self.get_session(session_id)
        turn = {
            "role": role,
            "content": content,
            "tool_calls": tool_calls or [],
            "tool_results": tool_results or {},
            "timestamp": time.time(),
        }
        session["turns"].append(turn)
        session["turn_count"] += 1
        session["updated_at"] = time.time()

        # Prune if exceeds max turns
        if len(session["turns"]) > self._max_turns:
            # Remove oldest turns, keeping the most recent
            excess = len(session["turns"]) - self._max_turns
            session["turns"] = session["turns"][excess:]

        return turn

    def get_context(self, session_id: str, max_recent: int = 10) -> List[Dict[str, str]]:
        """
        Get the conversation context as a list of messages for the LLM.

        Returns messages in the format expected by LLM clients:
        [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}, ...]
        """
        session = self.get_session(session_id)
        messages = []

        # Add system message with summary as context
        if session["summary"]:
            messages.append({
                "role": "system",
                "content": f"Conversation summary: {session['summary']}",
            })

        # Add recent turns
        recent_turns = session["turns"][-max_recent:] if max_recent > 0 else session["turns"]
        for turn in recent_turns:
            msg = {"role": turn["role"], "content": turn["content"]}
            messages.append(msg)

        return messages

    async def compress(self, session_id: str, llm_summarize_fn=None):
        """
        Compress old turns into a summary when the context is too long.

        Args:
            session_id: Session to compress
            llm_summarize_fn: Async function(summary, turns) -> new_summary
        """
        session = self.get_session(session_id)
        if len(session["turns"]) < 10:
            return  # No need to compress yet

        # Take oldest turns for summarization, keep newest raw
        compress_count = len(session["turns"]) - 5  # Keep 5 most recent raw
        if compress_count < 3:
            return

        old_turns = session["turns"][:compress_count]
        recent_turns = session["turns"][compress_count:]

        if llm_summarize_fn:
            try:
                old_text = "\n".join(
                    f"{t['role']}: {t['content'][:500]}"
                    for t in old_turns if t['content']
                )
                new_summary = await llm_summarize_fn(session["summary"], old_text)
                session["summary"] = new_summary
            except Exception as e:
                logger.error(f"Summary compression failed: {e}")
                # Fallback: just keep a basic summary
                session["summary"] = f"Conversation with {session['turn_count']} turns."
        else:
            # Simple truncation-based summary
            key_points = []
            for t in old_turns[-5:]:  # Last 5 of the old turns
                if t["role"] == "assistant" and t["content"]:
                    key_points.append(t["content"][:100])
            session["summary"] = "; ".join(key_points)

        session["turns"] = recent_turns

    def clear(self, session_id: str) -> None:
        """Clear all turns for a session."""
        if session_id in self._sessions:
            self._sessions[session_id]["turns"] = []
            self._sessions[session_id]["summary"] = ""
            self._sessions[session_id]["turn_count"] = 0

    def delete_session(self, session_id: str) -> None:
        """Delete a session entirely."""
        self._sessions.pop(session_id, None)

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all active sessions with metadata."""
        return [
            {
                "session_id": sid,
                "turn_count": s["turn_count"],
                "created_at": s["created_at"],
                "updated_at": s["updated_at"],
            }
            for sid, s in self._sessions.items()
        ]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize all sessions to dict."""
        return {
            "sessions": {
                sid: {
                    "turns": s["turns"],
                    "summary": s["summary"],
                    "turn_count": s["turn_count"],
                }
                for sid, s in self._sessions.items()
            }
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConversationMemory":
        """Load sessions from dict."""
        mem = cls()
        for sid, s in data.get("sessions", {}).items():
            mem._sessions[sid] = {
                "session_id": sid,
                "turns": s.get("turns", []),
                "summary": s.get("summary", ""),
                "turn_count": s.get("turn_count", len(s.get("turns", []))),
                "created_at": time.time(),
                "updated_at": time.time(),
            }
        return mem
