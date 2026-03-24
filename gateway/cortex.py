"""Cortex signal manager for gateway-owned external event delivery."""

from __future__ import annotations

import threading
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Deque, Dict, List, Optional


@dataclass
class SignalRecord:
    """A user-facing signal emitted by an external producer."""

    signal_id: str
    target_session_key: str
    source_type: str
    source_ref: str
    title: str
    summary: str
    priority: str = "normal"
    reason: str = "notify"
    action_items: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    delivered_at: Optional[datetime] = None
    state: str = "pending"

    def to_prompt_dict(self) -> Dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "title": self.title,
            "summary": self.summary,
            "priority": self.priority,
            "reason": self.reason,
            "action_items": list(self.action_items),
            "metadata": dict(self.metadata),
            "created_at": self.created_at.isoformat(),
        }


class SessionSignalManager:
    """Thread-safe in-memory queue of per-session Cortex signals."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._signals: Dict[str, SignalRecord] = {}
        self._queues: Dict[str, Deque[str]] = defaultdict(deque)

    def create_signal(
        self,
        *,
        target_session_key: str,
        source_type: str,
        source_ref: str,
        title: str,
        summary: str,
        priority: str = "normal",
        reason: str = "notify",
        action_items: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SignalRecord:
        signal = SignalRecord(
            signal_id=f"sig_{uuid.uuid4().hex[:12]}",
            target_session_key=target_session_key,
            source_type=source_type,
            source_ref=source_ref,
            title=title.strip(),
            summary=summary.strip(),
            priority=priority if priority in {"normal", "urgent"} else "normal",
            reason=reason if reason in {"notify", "approval_required", "input_required"} else "notify",
            action_items=[str(item).strip() for item in (action_items or []) if str(item).strip()],
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._signals[signal.signal_id] = signal
            self._queues[target_session_key].append(signal.signal_id)
        return signal

    def claim_pending(self, session_key: str) -> List[SignalRecord]:
        """Atomically claim and remove all pending signals for a session."""
        with self._lock:
            queue = self._queues.get(session_key)
            if not queue:
                return []
            claimed: List[SignalRecord] = []
            while queue:
                signal_id = queue.popleft()
                signal = self._signals.get(signal_id)
                if not signal or signal.state != "pending":
                    continue
                signal.state = "delivered"
                signal.delivered_at = datetime.now()
                claimed.append(signal)
            if not queue:
                self._queues.pop(session_key, None)
            return claimed

    def has_pending(self, session_key: str) -> bool:
        with self._lock:
            queue = self._queues.get(session_key)
            if not queue:
                return False
            return any(
                (signal := self._signals.get(signal_id)) and signal.state == "pending"
                for signal_id in queue
            )

    def pending_count(self, session_key: str) -> int:
        with self._lock:
            queue = self._queues.get(session_key)
            if not queue:
                return 0
            return sum(
                1
                for signal_id in queue
                if (signal := self._signals.get(signal_id)) and signal.state == "pending"
            )

    def get(self, signal_id: str) -> Optional[SignalRecord]:
        with self._lock:
            return self._signals.get(signal_id)
