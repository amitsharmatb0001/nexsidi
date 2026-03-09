"""Agent Message Bus: Valkey-backed inter-agent messaging for NexSidi pipelines.

Enables NexSidi agents (Shubham, Aanya, Vikram, Karan, etc.) to ask each other
questions during pipeline execution and wait for answers asynchronously.

Design decisions:
- Valkey (Redis-compatible) for low-latency message delivery across agents
- ask/answer protocol: BLPOP-based blocking wait (was: 0.5s poll loop)
- Redis List (RPUSH/LRANGE) for question queue routing per agent per pipeline
- Redis Hash for message metadata
- Redis List for answer rendezvous (RPUSH by answerer, BLPOP by waiter)
- All keys carry 1-hour TTL — no manual cleanup needed after pipeline ends
- Graceful degradation: raises AgentBusUnavailable on ConnectionError instead
  of crashing the pipeline; callers decide whether to retry or continue without
- Singleton pattern mirrors context_engine.py exactly: init_* / shutdown_* / get_*
- Shared Valkey pool (valkey_pool.py) — does NOT create its own connection pool

BLPOP vs polling:
  Old design polled a Redis String (GET answer_key) every 0.5 s in a Python
  while loop — 2 round-trips/second per waiting message. At 100 concurrent
  pipeline questions this becomes 200 Valkey round-trips/second of pure waste.
  BLPOP is a single server-side blocking command: the client sleeps in the
  kernel until the server pushes the element or the timeout fires. O(1) wakeup,
  zero idle round-trips.

Key structure in Valkey:
    agent_bus:{pipeline_run_id}:{to_agent}:questions
        → Redis List of message_id strings (RPUSH to enqueue, LRANGE to peek)
    agent_bus:msg:{message_id}
        → Redis Hash: all message fields (from, to, question, status, timestamps)
    agent_bus:{pipeline_run_id}:{message_id}:answer
        → Redis List with exactly one element: the answer text.
          answer() RPUSHes the answer; ask() BLPOPs it.
"""

from __future__ import annotations

import threading as _threading
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

# ── Constants ────────────────────────────────────────────────────────

_MSG_TTL_SECONDS = 3600          # 1 hour — all message keys expire automatically
_DEFAULT_ASK_TIMEOUT = 180.0     # P2-3: 3 min default (was 30s — too short for Shubham/Aanya LLM calls)
_AGENT_TIMEOUTS: dict[str, float] = {
    "shubham": 300.0,  # 5 min — large code generation
    "aanya": 300.0,    # 5 min — large frontend generation
    "fixer": 240.0,    # 4 min — iterative fix loops
}
_MAX_PENDING_SCAN = 5            # default max questions returned by get_pending_questions


# ── Exceptions ───────────────────────────────────────────────────────


class AgentMessageTimeout(Exception):
    """Raised by ask() when the answering agent does not reply within the timeout.

    Callers should handle this by either retrying, proceeding without the answer,
    or failing the pipeline step with a meaningful error message.
    """


class AgentBusUnavailable(Exception):
    """Raised when the Valkey connection is unavailable.

    Callers can catch this to degrade gracefully (e.g., skip inter-agent
    communication and use cached context instead).
    """


class AgentDeadlockDetected(Exception):
    """Raised when ask_agent() would create a circular wait cycle."""
    pass


class _AskTracker:
    """Tracks active ask_agent relationships to detect deadlock cycles.

    Maintains a directed graph: asking_agent -> target_agent.
    Before each ask, checks if adding the edge would create a cycle.
    Thread-safe via a simple lock (low contention -- ask() is rare).
    """

    def __init__(self) -> None:
        self._lock = _threading.Lock()
        self._active: dict[str, str] = {}  # from_agent -> to_agent

    def register_ask(self, from_agent: str, to_agent: str) -> None:
        """Register an ask. Raises AgentDeadlockDetected if cycle detected."""
        with self._lock:
            visited: set[str] = {from_agent}
            current = to_agent
            while current in self._active:
                if current in visited:
                    cycle = self._build_cycle_path(from_agent, to_agent)
                    raise AgentDeadlockDetected(
                        f"Deadlock detected: {' -> '.join(cycle)}"
                    )
                visited.add(current)
                current = self._active[current]
            self._active[from_agent] = to_agent

    def release_ask(self, from_agent: str) -> None:
        """Release an active ask (call in finally block)."""
        with self._lock:
            self._active.pop(from_agent, None)

    def _build_cycle_path(self, from_a: str, to_a: str) -> list[str]:
        path = [from_a, to_a]
        current = to_a
        while self._active.get(current) != from_a:
            current = self._active[current]
            path.append(current)
        path.append(from_a)
        return path


_ask_tracker = _AskTracker()


# ── Phase 1A: Agent Group Definitions ────────────────────────────────

AGENT_GROUPS: dict[str, list[str]] = {
    "design_team": ["vikram", "dhruv", "vanya"],
    "build_team": ["shubham", "aanya"],
    "quality_team": ["karan", "navya", "deepika", "attack_tester", "aarav"],
    "leadership": ["tilotma", "vikram"],
}

# ── Phase 1A: Run Key Registry ────────────────────────────────────
# Maps pipeline_run_id → decrypted per-run Fernet key bytes.
# Populated by pipeline.py at run start, purged at terminal state.
_RUN_KEYS: dict[str, bytes] = {}


def register_run_key(run_id: str, run_key: bytes) -> None:
    """Store a per-run encryption key (called by pipeline at start)."""
    _RUN_KEYS[run_id] = run_key


def purge_run_key(run_id: str) -> None:
    """Remove a per-run encryption key (called by pipeline at end)."""
    _RUN_KEYS.pop(run_id, None)


def get_run_key(run_id: str) -> bytes | None:
    """Retrieve the per-run encryption key, or None if not set."""
    return _RUN_KEYS.get(run_id)


# ── Core class ───────────────────────────────────────────────────────


class AgentMessageBus:
    """Valkey-backed inter-agent messaging bus for NexSidi pipeline agents.

    Agents send questions to each other via ask(), answer pending questions
    via answer(), and inspect their inbound queue via get_pending_questions().

    Phase 1A: All message payloads are encrypted with per-run Fernet keys
    before storage in Valkey. Even if Valkey is compromised, message contents
    are unreadable without the run key.

    Thread safety: This class is designed for a single asyncio event loop.
    All public methods are coroutines and must be awaited.

    Usage example::

        bus = get_agent_message_bus()

        # Shubham asks Vikram a question:
        answer = await bus.ask(
            from_agent="shubham",
            to_agent="vikram",
            pipeline_run_id=run_id,
            question="Should the auth service use JWT or sessions?",
            context={"project_type": "SaaS", "user_scale": 50000},
        )

        # Vikram's handler fetches and answers pending questions:
        questions = await bus.get_pending_questions("vikram", run_id)
        for q in questions:
            await bus.answer("vikram", run_id, q["message_id"], "Use JWT — stateless.")
    """

    def __init__(self, redis_client: Any) -> None:
        """Initialize with an async Redis/Valkey client.

        Args:
            redis_client: aioredis-compatible async client (redis.asyncio.Redis).
                          Obtained from the shared valkey_pool, not created here.
        """
        self._redis = redis_client
        self._logger = structlog.get_logger(__name__)

    # ── Phase 1A: Encryption Helpers ─────────────────────────────────

    def _encrypt_field(self, value: str, run_id: str) -> str:
        """Encrypt a message field if encryption is enabled and key exists.

        Falls back to plaintext if encryption is disabled or key missing
        (graceful degradation — never break message delivery).
        """
        try:
            from app.config import get_settings
            if not get_settings().agent_message_encryption:
                return value
        except Exception:
            return value

        run_key = get_run_key(run_id)
        if not run_key:
            return value

        try:
            from app.services.encryption import get_message_encryptor
            return get_message_encryptor().encrypt(value, run_key)
        except Exception as exc:
            self._logger.debug("encrypt_field_failed", error=str(exc)[:100])
            return value  # Graceful fallback

    def _decrypt_field(self, value: str, run_id: str) -> str:
        """Decrypt a message field if it appears to be encrypted.

        Falls back to returning as-is if decryption fails or value is plaintext.
        """
        try:
            from app.services.encryption import MessageEncryptor
            if not MessageEncryptor.is_encrypted(value):
                return value
        except Exception:
            return value

        run_key = get_run_key(run_id)
        if not run_key:
            return value

        try:
            from app.services.encryption import get_message_encryptor
            return get_message_encryptor().decrypt(value, run_key)
        except Exception as exc:
            self._logger.debug("decrypt_field_failed", error=str(exc)[:100])
            return value  # Return as-is on failure

    # ── Key builders ─────────────────────────────────────────────────

    @staticmethod
    def _queue_key(pipeline_run_id: str, agent_name: str) -> str:
        """Redis List key for an agent's inbound question queue."""
        return f"agent_bus:{pipeline_run_id}:{agent_name}:questions"

    @staticmethod
    def _msg_key(message_id: str) -> str:
        """Redis Hash key for message metadata."""
        return f"agent_bus:msg:{message_id}"

    @staticmethod
    def _answer_key(pipeline_run_id: str, message_id: str) -> str:
        """Redis String key written by the answering agent."""
        return f"agent_bus:{pipeline_run_id}:{message_id}:answer"

    # ── Public API ───────────────────────────────────────────────────

    async def ask(
        self,
        from_agent: str,
        to_agent: str,
        pipeline_run_id: str,
        question: str,
        context: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> str:
        """Send a question to another agent and block until an answer arrives.

        Stores the question as a Redis Hash, pushes the message_id onto the
        to_agent's question queue, then uses BLPOP on the answer rendezvous
        List to block until the answering agent pushes the reply.

        BLPOP replaces the old 0.5-second polling loop — zero idle round-trips
        instead of 2 Valkey calls/second per waiting message.

        Args:
            from_agent: Name of the asking agent (e.g. "shubham").
            to_agent:   Name of the agent that should answer (e.g. "vikram").
            pipeline_run_id: UUID string identifying the current pipeline run.
            question:   Free-text question for the receiving agent.
            context:    Optional dict of additional context for the question
                        (e.g. relevant artifacts, constraints). Stored as JSON.
            timeout:    Seconds to wait for an answer. If None, uses per-agent
                        default from _AGENT_TIMEOUTS or _DEFAULT_ASK_TIMEOUT.

        Returns:
            The answer string provided by the receiving agent.

        Raises:
            AgentMessageTimeout:  No answer arrived within ``timeout`` seconds.
                                  The message status in Valkey is set to "timed_out".
            AgentBusUnavailable:  Valkey is not reachable (ConnectionError).
        """
        # P2-3: Per-agent timeout resolution
        if timeout is None:
            timeout = _AGENT_TIMEOUTS.get(to_agent, _DEFAULT_ASK_TIMEOUT)

        import orjson  # Local import — keeps top-level imports minimal

        message_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # Phase 1A: Encrypt sensitive payload fields before Valkey storage
        _enc_question = self._encrypt_field(question, pipeline_run_id)
        _enc_context = self._encrypt_field(
            orjson.dumps(context or {}).decode("utf-8"), pipeline_run_id
        )

        msg_data: dict[str, str] = {
            "message_id": message_id,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "pipeline_run_id": pipeline_run_id,
            "question": _enc_question,
            "context": _enc_context,
            "asked_at": now,
            "answer": "",
            "answered_at": "",
            "status": "pending",
        }

        msg_key = self._msg_key(message_id)
        queue_key = self._queue_key(pipeline_run_id, to_agent)
        answer_key = self._answer_key(pipeline_run_id, message_id)

        try:
            # Store message hash and enqueue in a single pipeline for atomicity.
            # If the process crashes after hset but before rpush, the message
            # will be orphaned in the hash but never delivered — acceptable
            # because the TTL will clean it up and the caller gets a timeout.
            pipe = self._redis.pipeline(transaction=True)
            pipe.hset(msg_key, mapping=msg_data)
            pipe.expire(msg_key, _MSG_TTL_SECONDS)
            pipe.rpush(queue_key, message_id)
            pipe.expire(queue_key, _MSG_TTL_SECONDS)
            # PHASE-3: Track in transcript for oversight
            transcript_key = f"agent_bus:{pipeline_run_id}:transcript"
            pipe.rpush(transcript_key, message_id)
            pipe.expire(transcript_key, _MSG_TTL_SECONDS)
            await pipe.execute()
        except Exception as exc:
            # Distinguish connection failures from other errors.
            if _is_connection_error(exc):
                self._logger.warning(
                    "agent_bus_unavailable_on_ask",
                    from_agent=from_agent,
                    to_agent=to_agent,
                    pipeline_run_id=pipeline_run_id,
                    error=str(exc),
                )
                raise AgentBusUnavailable(
                    f"Valkey unavailable — cannot send question from {from_agent} to {to_agent}: {exc}"
                ) from exc
            raise

        self._logger.info(
            "agent_question_sent",
            message_id=message_id,
            from_agent=from_agent,
            to_agent=to_agent,
            pipeline_run_id=pipeline_run_id,
            question_preview=question[:80],
            timeout=timeout,
        )

        # Deadlock prevention: register this ask and check for cycles
        # before blocking on BLPOP. If A is waiting on B and B tries to
        # ask A, the tracker detects the cycle and raises immediately.
        try:
            _ask_tracker.register_ask(from_agent, to_agent)
        except AgentDeadlockDetected as exc:
            self._logger.error(
                "agent_deadlock_detected",
                from_agent=from_agent,
                to_agent=to_agent,
                pipeline_run_id=pipeline_run_id,
                message_id=message_id,
                error=str(exc),
            )
            raise

        # BLPOP blocks until answer() pushes to the answer_key List, or timeout fires.
        # This replaces the old 0.5s polling loop — O(1) server-side wakeup, zero idle
        # round-trips.  BLPOP returns (key, value) on success or None on timeout.
        # timeout must be an integer for Valkey; we round up to not lose precision.
        blpop_timeout = max(1, int(timeout))
        try:
            try:
                blpop_result = await self._redis.blpop(answer_key, timeout=blpop_timeout)
            except Exception as exc:
                if _is_connection_error(exc):
                    self._logger.warning(
                        "agent_bus_unavailable_during_blpop",
                        message_id=message_id,
                        error=str(exc),
                    )
                    raise AgentBusUnavailable(
                        f"Valkey unavailable while waiting for answer to {message_id}: {exc}"
                    ) from exc
                raise

            if blpop_result is None:
                # AUDIT-T2-9: Check for late answer before declaring timeout.
                # If answer arrived just after BLPOP expired, recover it to avoid
                # duplicate/stale answers sitting in Valkey for the next retry.
                try:
                    late = await self._redis.lpop(answer_key)
                    if late:
                        self._logger.info("ask_late_answer_recovered", message_id=message_id)
                        _late_text = late.decode("utf-8") if isinstance(late, bytes) else late
                        return self._decrypt_field(_late_text, pipeline_run_id)
                    await self._redis.hset(msg_key, "status", "timed_out")
                except Exception:
                    pass  # Best-effort status update; TTL will clean up regardless.

                self._logger.warning(
                    "agent_question_timed_out",
                    message_id=message_id,
                    from_agent=from_agent,
                    to_agent=to_agent,
                    pipeline_run_id=pipeline_run_id,
                    timeout_seconds=timeout,
                )
                raise AgentMessageTimeout(
                    f"No answer from agent '{to_agent}' within {timeout}s "
                    f"(message_id={message_id}, pipeline={pipeline_run_id})"
                )

            # blpop_result is a (key_bytes, value_bytes) tuple
            _, raw_answer = blpop_result
            answer_text = (
                raw_answer.decode("utf-8") if isinstance(raw_answer, bytes) else raw_answer
            )
            # Phase 1A: Decrypt the answer payload
            answer_text = self._decrypt_field(answer_text, pipeline_run_id)
            self._logger.info(
                "agent_question_answered",
                message_id=message_id,
                from_agent=from_agent,
                to_agent=to_agent,
                pipeline_run_id=pipeline_run_id,
            )
            return answer_text
        finally:
            _ask_tracker.release_ask(from_agent)

    async def answer(
        self,
        agent_name: str,
        pipeline_run_id: str,
        message_id: str,
        answer: str,
    ) -> None:
        """Submit an answer to a pending question.

        Validates that the message exists and was addressed to ``agent_name``,
        stores the answer in the rendezvous key (which unblocks the waiting
        ask() call), and updates the message hash with status and timestamp.

        Args:
            agent_name:      Name of the agent submitting the answer. Must match
                             the ``to_agent`` field on the original question.
            pipeline_run_id: UUID string of the pipeline run.
            message_id:      UUID of the question being answered.
            answer:          The answer text to return to the asking agent.

        Raises:
            ValueError:          Message not found, or ``agent_name`` does not
                                 match the ``to_agent`` on the original question.
            AgentBusUnavailable: Valkey is not reachable.
        """
        msg_key = self._msg_key(message_id)
        answer_key = self._answer_key(pipeline_run_id, message_id)
        now = datetime.now(timezone.utc).isoformat()

        try:
            # Fetch relevant fields in one round-trip to validate ownership.
            fields = await self._redis.hmget(msg_key, "to_agent", "status")
        except Exception as exc:
            if _is_connection_error(exc):
                self._logger.warning(
                    "agent_bus_unavailable_on_answer",
                    agent_name=agent_name,
                    message_id=message_id,
                    error=str(exc),
                )
                raise AgentBusUnavailable(
                    f"Valkey unavailable — cannot submit answer for {message_id}: {exc}"
                ) from exc
            raise

        # fields is a list: [to_agent_raw, status_raw]
        to_agent_raw, status_raw = fields

        if to_agent_raw is None:
            raise ValueError(
                f"Message {message_id} not found in Valkey "
                f"(may have expired or never existed)"
            )

        stored_to_agent = (
            to_agent_raw.decode("utf-8") if isinstance(to_agent_raw, bytes) else to_agent_raw
        )
        if stored_to_agent != agent_name:
            raise ValueError(
                f"Agent '{agent_name}' cannot answer message {message_id}: "
                f"message was addressed to '{stored_to_agent}'"
            )

        stored_status = (
            status_raw.decode("utf-8") if isinstance(status_raw, bytes) else (status_raw or "")
        )
        if stored_status == "timed_out":
            # The asker already gave up — still store the answer (the asker may
            # inspect the hash for debugging), but log a warning.
            self._logger.warning(
                "agent_answering_timed_out_question",
                message_id=message_id,
                agent_name=agent_name,
                pipeline_run_id=pipeline_run_id,
            )

        try:
            # Phase 1A: Encrypt the answer payload
            # Need pipeline_run_id for encryption — fetch from the message hash
            _run_id_raw = await self._redis.hget(msg_key, "pipeline_run_id")
            _run_id = (
                _run_id_raw.decode("utf-8") if isinstance(_run_id_raw, bytes) else (_run_id_raw or pipeline_run_id)
            )
            _enc_answer = self._encrypt_field(answer, _run_id)

            # RPUSH to the answer rendezvous List FIRST — this is what wakes the
            # BLPOP in ask(). Then update the hash in the same pipeline for audit.
            # Even if the hash update fails, the answer is delivered (RPUSH ran first).
            pipe = self._redis.pipeline(transaction=True)
            pipe.rpush(answer_key, _enc_answer)
            pipe.expire(answer_key, _MSG_TTL_SECONDS)
            pipe.hset(
                msg_key,
                mapping={
                    "answer": _enc_answer,
                    "answered_at": now,
                    "status": "answered",
                },
            )
            pipe.expire(msg_key, _MSG_TTL_SECONDS)
            await pipe.execute()
        except Exception as exc:
            if _is_connection_error(exc):
                self._logger.warning(
                    "agent_bus_unavailable_writing_answer",
                    agent_name=agent_name,
                    message_id=message_id,
                    error=str(exc),
                )
                raise AgentBusUnavailable(
                    f"Valkey unavailable — answer for {message_id} not saved: {exc}"
                ) from exc
            raise

        self._logger.info(
            "agent_answer_submitted",
            message_id=message_id,
            agent_name=agent_name,
            pipeline_run_id=pipeline_run_id,
            answer_preview=answer[:80],
        )

    async def get_pending_questions(
        self,
        agent_name: str,
        pipeline_run_id: str,
        max_questions: int = _MAX_PENDING_SCAN,
    ) -> list[dict[str, Any]]:
        """Return pending (unanswered) questions for an agent in this pipeline.

        Non-blocking — does NOT pop from the queue. Questions remain in the
        queue until explicitly answered via answer(). This allows an agent to
        inspect its question backlog without committing to answer any of them.

        Implementation: reads up to ``max_questions`` message IDs from the
        head of the queue list, fetches their hashes, and filters for those
        whose status is still "pending".

        Args:
            agent_name:      Name of the agent to check questions for.
            pipeline_run_id: UUID string of the pipeline run.
            max_questions:   Maximum number of queue entries to inspect
                             (default 5). Limits Valkey read cost.

        Returns:
            List of message dicts (see module docstring for schema).
            Each dict has keys: message_id, from_agent, to_agent,
            pipeline_run_id, question, context (parsed dict), asked_at,
            answer, answered_at, status.

            Returns empty list if Valkey is unavailable (graceful degradation).

        Raises:
            AgentBusUnavailable: Valkey is not reachable.
        """
        import orjson

        queue_key = self._queue_key(pipeline_run_id, agent_name)

        try:
            # LRANGE is non-destructive — leaves the queue intact.
            raw_ids = await self._redis.lrange(queue_key, 0, max_questions - 1)
        except Exception as exc:
            if _is_connection_error(exc):
                self._logger.warning(
                    "agent_bus_unavailable_get_pending",
                    agent_name=agent_name,
                    pipeline_run_id=pipeline_run_id,
                    error=str(exc),
                )
                raise AgentBusUnavailable(
                    f"Valkey unavailable — cannot fetch pending questions for {agent_name}: {exc}"
                ) from exc
            raise

        if not raw_ids:
            return []

        message_ids = [
            raw_id.decode("utf-8") if isinstance(raw_id, bytes) else raw_id
            for raw_id in raw_ids
        ]

        # Batch-fetch all message hashes with a single pipeline to avoid
        # N sequential round-trips (same rationale as context_engine.py mget).
        try:
            pipe = self._redis.pipeline(transaction=False)
            for mid in message_ids:
                pipe.hgetall(self._msg_key(mid))
            results = await pipe.execute()
        except Exception as exc:
            if _is_connection_error(exc):
                self._logger.warning(
                    "agent_bus_unavailable_fetching_hashes",
                    agent_name=agent_name,
                    pipeline_run_id=pipeline_run_id,
                    error=str(exc),
                )
                raise AgentBusUnavailable(
                    f"Valkey unavailable — cannot fetch message hashes: {exc}"
                ) from exc
            raise

        pending: list[dict[str, Any]] = []
        for mid, raw_hash in zip(message_ids, results):
            if not raw_hash:
                # Message expired or never stored — skip silently.
                self._logger.debug(
                    "agent_bus_message_expired_or_missing",
                    message_id=mid,
                    agent_name=agent_name,
                )
                continue

            # Decode bytes values from the hash.
            decoded: dict[str, Any] = {
                (k.decode("utf-8") if isinstance(k, bytes) else k): (
                    v.decode("utf-8") if isinstance(v, bytes) else v
                )
                for k, v in raw_hash.items()
            }

            if decoded.get("status") != "pending":
                continue  # Already answered or timed out — skip.

            # Phase 1A: Decrypt encrypted fields
            _msg_run_id = decoded.get("pipeline_run_id", pipeline_run_id)
            if decoded.get("question"):
                decoded["question"] = self._decrypt_field(decoded["question"], _msg_run_id)

            # Parse the context JSON string back to a dict.
            raw_context = decoded.get("context", "{}")
            # Phase 1A: Decrypt context if encrypted
            raw_context = self._decrypt_field(raw_context, _msg_run_id)
            try:
                decoded["context"] = orjson.loads(raw_context)
            except (ValueError, TypeError):
                decoded["context"] = {}

            # Normalise optional fields that may be empty strings.
            _raw_answer = decoded.get("answer") or None
            if _raw_answer:
                _raw_answer = self._decrypt_field(_raw_answer, _msg_run_id)
            decoded["answer"] = _raw_answer
            decoded["answered_at"] = decoded.get("answered_at") or None

            pending.append(decoded)

        self._logger.debug(
            "agent_pending_questions_fetched",
            agent_name=agent_name,
            pipeline_run_id=pipeline_run_id,
            pending_count=len(pending),
            scanned=len(message_ids),
        )
        return pending

    # ── PHASE-3: Broadcast, Transcript, Priority ─────────────────────

    async def broadcast(
        self,
        from_agent: str,
        pipeline_run_id: str,
        message: str,
        to_agents: list[str] | None = None,
        cc_agents: list[str] | None = None,
        priority: str = "NORMAL",
    ) -> list[str]:
        """Broadcast a message to multiple agents (+ CC oversight agents).

        Unlike ask(), broadcast is fire-and-forget — no blocking wait for answers.
        Returns list of message_ids sent.

        Args:
            from_agent: Sending agent name.
            pipeline_run_id: Pipeline run ID.
            message: The message content.
            to_agents: List of agents to send to. If None, sends to all known agents.
            cc_agents: Additional agents to CC (e.g., ["tilotma"] for oversight).
            priority: "CRITICAL", "NORMAL", or "INFO".
        """
        import orjson

        _ALL_AGENTS = [
            "tilotma", "vikram", "saanvi", "vanya", "dhruv",
            "shubham", "aanya", "karan", "fixer", "pranav",
        ]

        # PHASE-3: Always CC both tilotma (Chief AI Official) and vikram
        # (second-in-command) on ALL broadcasts — they must be in the loop.
        _OVERSIGHT_AGENTS = ["tilotma", "vikram"]

        recipients = list(to_agents or _ALL_AGENTS)
        if cc_agents:
            for cc in cc_agents:
                if cc not in recipients:
                    recipients.append(cc)
        # Ensure oversight agents are ALWAYS included
        for oversight in _OVERSIGHT_AGENTS:
            if oversight not in recipients:
                recipients.append(oversight)

        # Remove sender from recipients
        recipients = [a for a in recipients if a != from_agent]

        message_ids: list[str] = []
        now = datetime.now(timezone.utc).isoformat()

        # Phase 1A: Pre-encrypt the shared payload once (same for all recipients)
        _enc_message = self._encrypt_field(message, pipeline_run_id)
        _enc_broadcast_ctx = self._encrypt_field(
            orjson.dumps({"priority": priority, "type": "broadcast"}).decode(),
            pipeline_run_id,
        )

        for to_agent in recipients:
            message_id = str(uuid.uuid4())
            msg_data = {
                "message_id": message_id,
                "from_agent": from_agent,
                "to_agent": to_agent,
                "pipeline_run_id": pipeline_run_id,
                "question": _enc_message,
                "context": _enc_broadcast_ctx,
                "asked_at": now,
                "answer": "",
                "answered_at": "",
                "status": "broadcast",
                "priority": priority,
            }

            msg_key = self._msg_key(message_id)
            queue_key = self._queue_key(pipeline_run_id, to_agent)

            try:
                pipe = self._redis.pipeline(transaction=True)
                pipe.hset(msg_key, mapping=msg_data)
                pipe.expire(msg_key, _MSG_TTL_SECONDS)
                pipe.rpush(queue_key, message_id)
                pipe.expire(queue_key, _MSG_TTL_SECONDS)
                # Also add to transcript
                transcript_key = f"agent_bus:{pipeline_run_id}:transcript"
                pipe.rpush(transcript_key, message_id)
                pipe.expire(transcript_key, _MSG_TTL_SECONDS)
                await pipe.execute()
                message_ids.append(message_id)
            except Exception as exc:
                if _is_connection_error(exc):
                    self._logger.warning("broadcast_failed", to_agent=to_agent, error=str(exc)[:100])
                    continue
                raise

        self._logger.info(
            "agent_broadcast_sent",
            from_agent=from_agent,
            recipients=len(message_ids),
            priority=priority,
            pipeline_run_id=pipeline_run_id,
        )
        return message_ids

    async def get_transcript(
        self,
        pipeline_run_id: str,
        max_messages: int = 100,
    ) -> list[dict[str, Any]]:
        """Get full message transcript for a pipeline run (for oversight).

        Returns all messages in chronological order.
        """
        import orjson

        transcript_key = f"agent_bus:{pipeline_run_id}:transcript"

        try:
            raw_ids = await self._redis.lrange(transcript_key, 0, max_messages - 1)
        except Exception as exc:
            if _is_connection_error(exc):
                return []
            raise

        if not raw_ids:
            return []

        message_ids = [
            raw_id.decode("utf-8") if isinstance(raw_id, bytes) else raw_id
            for raw_id in raw_ids
        ]

        try:
            pipe = self._redis.pipeline(transaction=False)
            for mid in message_ids:
                pipe.hgetall(self._msg_key(mid))
            results = await pipe.execute()
        except Exception:
            return []

        transcript: list[dict[str, Any]] = []
        for mid, raw_hash in zip(message_ids, results):
            if not raw_hash:
                continue
            decoded = {
                (k.decode("utf-8") if isinstance(k, bytes) else k): (
                    v.decode("utf-8") if isinstance(v, bytes) else v
                )
                for k, v in raw_hash.items()
            }
            # Phase 1A: Decrypt encrypted fields
            _t_run_id = decoded.get("pipeline_run_id", pipeline_run_id)
            if decoded.get("question"):
                decoded["question"] = self._decrypt_field(decoded["question"], _t_run_id)
            if decoded.get("answer"):
                decoded["answer"] = self._decrypt_field(decoded["answer"], _t_run_id)

            raw_ctx = decoded.get("context", "{}")
            raw_ctx = self._decrypt_field(raw_ctx, _t_run_id)
            try:
                decoded["context"] = orjson.loads(raw_ctx)
            except (ValueError, TypeError):
                decoded["context"] = {}
            transcript.append(decoded)

        return transcript

    async def report_error(
        self,
        from_agent: str,
        to_agent: str,
        pipeline_run_id: str,
        error_type: str,
        file_path: str,
        description: str,
        severity: str = "CRITICAL",
    ) -> str:
        """Report an error found by one agent to another (e.g., Aanya → Shubham).

        This is a convenience wrapper around ask() for structured error reports.
        Returns the response from the target agent.
        """
        question = (
            f"[ERROR REPORT — {severity}]\n"
            f"Type: {error_type}\n"
            f"File: {file_path}\n"
            f"Description: {description}\n\n"
            f"Please fix this issue."
        )

        # CC both tilotma AND vikram for oversight (PHASE-3 enforcement)
        try:
            await self.broadcast(
                from_agent=from_agent,
                pipeline_run_id=pipeline_run_id,
                message=f"{from_agent} reported {severity} error to {to_agent}: {description[:200]}",
                to_agents=["tilotma", "vikram"],
                priority=severity,
            )
        except Exception:
            pass  # Best-effort CC

        return await self.ask(
            from_agent=from_agent,
            to_agent=to_agent,
            pipeline_run_id=pipeline_run_id,
            question=question,
            context={"error_type": error_type, "file_path": file_path, "severity": severity},
        )

    # ── PHASE-3: Authority Messages ──────────────────────────────────

    async def send_authority_message(
        self,
        from_agent: str,
        pipeline_run_id: str,
        message: str,
        to_agents: list[str] | None = None,
    ) -> list[str]:
        """Send an AUTHORITY-level message from Tilotma or Vikram.

        AUTHORITY messages are the highest priority and CANNOT be ignored
        by any agent. Only Tilotma (Chief AI Official) and Vikram
        (second-in-command) can send these.

        Args:
            from_agent: Must be "tilotma" or "vikram".
            pipeline_run_id: Pipeline run ID.
            message: The authority directive.
            to_agents: Specific agents, or None for all agents.

        Returns:
            List of message_ids sent.

        Raises:
            ValueError: If from_agent is not tilotma or vikram.
        """
        if from_agent not in ("tilotma", "vikram"):
            raise ValueError(
                f"Only tilotma or vikram can send AUTHORITY messages, not '{from_agent}'"
            )

        self._logger.info(
            "authority_message_sent",
            from_agent=from_agent,
            pipeline_run_id=pipeline_run_id,
            message_preview=message[:100],
        )

        return await self.broadcast(
            from_agent=from_agent,
            pipeline_run_id=pipeline_run_id,
            message=f"[AUTHORITY — {from_agent.upper()}] {message}",
            to_agents=to_agents,
            priority="AUTHORITY",
        )

    async def get_unread_authority_messages(
        self,
        agent_name: str,
        pipeline_run_id: str,
    ) -> list[dict[str, Any]]:
        """Check if an agent has unread AUTHORITY messages.

        Used by pipeline to enforce that agents don't ignore authority
        messages from Tilotma/Vikram.
        """
        messages = await self.get_pending_questions(agent_name, pipeline_run_id, max_questions=20)
        authority: list[dict[str, Any]] = []
        for msg in messages:
            priority = msg.get("priority", "")
            from_agent = msg.get("from_agent", "")
            # Check both explicit AUTHORITY priority and messages from oversight agents
            if priority == "AUTHORITY" or from_agent in ("tilotma", "vikram"):
                ctx = msg.get("context", {})
                if isinstance(ctx, str):
                    try:
                        import orjson
                        ctx = orjson.loads(ctx)
                    except Exception:
                        ctx = {}
                if ctx.get("priority") == "AUTHORITY" or priority == "AUTHORITY":
                    authority.append(msg)
        return authority

    # ── Phase 1A: Group Channels ─────────────────────────────────────

    async def send_group(
        self,
        from_agent: str,
        pipeline_run_id: str,
        group_name: str,
        message: str,
        priority: str = "NORMAL",
    ) -> list[str]:
        """Send a message to a predefined agent group.

        Resolves the group name to a list of agents using AGENT_GROUPS,
        then broadcasts to all members. If the sender is in the group,
        they are excluded from recipients.

        Args:
            from_agent: Sending agent name.
            pipeline_run_id: Pipeline run ID.
            group_name: One of: "design_team", "build_team", "quality_team", "leadership".
            message: The message content.
            priority: Message priority level.

        Returns:
            List of message_ids sent.

        Raises:
            ValueError: If group_name is not a known group.
        """
        if group_name not in AGENT_GROUPS:
            raise ValueError(
                f"Unknown agent group '{group_name}'. "
                f"Valid groups: {', '.join(sorted(AGENT_GROUPS.keys()))}"
            )

        recipients = AGENT_GROUPS[group_name]
        group_message = f"[GROUP:{group_name.upper()}] {message}"

        return await self.broadcast(
            from_agent=from_agent,
            pipeline_run_id=pipeline_run_id,
            message=group_message,
            to_agents=recipients,
            priority=priority,
        )


# ── Internal helper ──────────────────────────────────────────────────


def _is_connection_error(exc: Exception) -> bool:
    """Return True if ``exc`` indicates a Valkey/Redis connection failure.

    Catches both ``redis.exceptions.ConnectionError`` and the built-in
    ``ConnectionError`` so callers get AgentBusUnavailable regardless of
    which layer raises first.
    """
    # redis.exceptions.ConnectionError is a subclass of OSError, which is a
    # subclass of the built-in ConnectionError — so a single isinstance check
    # covers both the library-specific and the stdlib variants.
    try:
        import redis.exceptions as _redis_exc
        if isinstance(exc, (_redis_exc.ConnectionError, _redis_exc.TimeoutError)):
            return True
    except ImportError:
        pass  # Expected: optional dependency not installed
    return isinstance(exc, (ConnectionError, TimeoutError))


# ── Singleton ────────────────────────────────────────────────────────

_bus: AgentMessageBus | None = None


def get_agent_message_bus() -> AgentMessageBus:
    """Return the AgentMessageBus singleton.

    Must be called after init_agent_message_bus() has completed.

    Raises:
        RuntimeError: If init_agent_message_bus() was never called.
    """
    global _bus
    if _bus is None:
        raise RuntimeError(
            "AgentMessageBus not initialized. Call init_agent_message_bus() first."
        )
    return _bus


async def init_agent_message_bus() -> AgentMessageBus:
    """Initialize the AgentMessageBus singleton with the shared Valkey client.

    Uses the same shared Valkey pool as context_engine and prompt_engine —
    does NOT create a separate connection pool.

    Idempotent: returns the existing instance if already initialized.

    Called once at app startup (in main.py lifespan).

    Returns:
        The initialized AgentMessageBus instance.

    Raises:
        AgentBusUnavailable: Valkey is not reachable at startup time.
        Exception: Any unexpected error from the Valkey client setup.
    """
    global _bus
    if _bus is not None:
        return _bus

    _logger = structlog.get_logger(__name__)

    from app.services.valkey_pool import get_valkey_client

    try:
        client = await get_valkey_client()
    except Exception as exc:
        if _is_connection_error(exc):
            _logger.warning(
                "agent_message_bus_valkey_unavailable",
                error=str(exc),
            )
            raise AgentBusUnavailable(
                f"Cannot initialize AgentMessageBus — Valkey unreachable: {exc}"
            ) from exc
        raise

    _bus = AgentMessageBus(client)
    _logger.info("agent_message_bus_initialized")
    return _bus


async def shutdown_agent_message_bus() -> None:
    """Shut down the AgentMessageBus singleton.

    Does NOT close the Valkey client — it is shared and will be closed by
    shutdown_valkey_client() in main.py after all services have shut down.

    Called at app shutdown (in main.py lifespan).
    """
    global _bus
    _bus = None
    structlog.get_logger(__name__).info("agent_message_bus_shutdown")


def reset_agent_message_bus() -> None:
    """Reset the singleton for testing or event loop changes.

    Mirrors reset_context_engine() — intended for use in test teardown only.
    Does NOT close any connections.
    """
    global _bus
    _bus = None
