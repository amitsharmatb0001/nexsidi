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

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

# ── Constants ────────────────────────────────────────────────────────

_MSG_TTL_SECONDS = 3600          # 1 hour — all message keys expire automatically
_DEFAULT_ASK_TIMEOUT = 30.0      # seconds before ask() raises AgentMessageTimeout
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


# ── Core class ───────────────────────────────────────────────────────


class AgentMessageBus:
    """Valkey-backed inter-agent messaging bus for NexSidi pipeline agents.

    Agents send questions to each other via ask(), answer pending questions
    via answer(), and inspect their inbound queue via get_pending_questions().

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
        timeout: float = _DEFAULT_ASK_TIMEOUT,
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
            timeout:    Seconds to wait for an answer (default 30s).

        Returns:
            The answer string provided by the receiving agent.

        Raises:
            AgentMessageTimeout:  No answer arrived within ``timeout`` seconds.
                                  The message status in Valkey is set to "timed_out".
            AgentBusUnavailable:  Valkey is not reachable (ConnectionError).
        """
        import orjson  # Local import — keeps top-level imports minimal

        message_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        msg_data: dict[str, str] = {
            "message_id": message_id,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "pipeline_run_id": pipeline_run_id,
            "question": question,
            "context": orjson.dumps(context or {}).decode("utf-8"),
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

        # BLPOP blocks until answer() pushes to the answer_key List, or timeout fires.
        # This replaces the old 0.5s polling loop — O(1) server-side wakeup, zero idle
        # round-trips.  BLPOP returns (key, value) on success or None on timeout.
        # timeout must be an integer for Valkey; we round up to not lose precision.
        blpop_timeout = max(1, int(timeout))
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
            # Timeout — mark the message and raise.
            try:
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
        self._logger.info(
            "agent_question_answered",
            message_id=message_id,
            from_agent=from_agent,
            to_agent=to_agent,
            pipeline_run_id=pipeline_run_id,
        )
        return answer_text

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
            # RPUSH to the answer rendezvous List FIRST — this is what wakes the
            # BLPOP in ask(). Then update the hash in the same pipeline for audit.
            # Even if the hash update fails, the answer is delivered (RPUSH ran first).
            pipe = self._redis.pipeline(transaction=True)
            pipe.rpush(answer_key, answer)
            pipe.expire(answer_key, _MSG_TTL_SECONDS)
            pipe.hset(
                msg_key,
                mapping={
                    "answer": answer,
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

            # Parse the context JSON string back to a dict.
            raw_context = decoded.get("context", "{}")
            try:
                decoded["context"] = orjson.loads(raw_context)
            except (ValueError, TypeError):
                decoded["context"] = {}

            # Normalise optional fields that may be empty strings.
            decoded["answer"] = decoded.get("answer") or None
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
        pass
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
