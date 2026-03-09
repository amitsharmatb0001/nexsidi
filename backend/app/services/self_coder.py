"""SelfCoder -- NexSidi's self-evolution engine.

Allows agents to read, modify, and extend their own source code at runtime.
This is the core of NexSidi's autonomous capability: agents can introspect
the codebase, propose changes, and -- after validation -- apply them.

SAFETY PROTOCOL (IMMUTABLE — AGENTS CANNOT MODIFY THIS SECTION):
───────────────────────────────────────────────────────────────────
1. Protected files CANNOT be modified by agents under ANY circumstances.
   These include: self_coder.py, secret_manager.py, security_guardian.py,
   rate_limiter.py, karan.py (security auditor), attack_tester.py, and
   any file containing authentication/authorization logic.

2. Protected code patterns (safety checks, auth validators, rate limiters,
   encryption, token handling) CANNOT be modified even in non-protected files.

3. Any change that touches a protected area is BLOCKED immediately.
   Status is set to "requires_human_review". ONLY a human engineer
   can approve these changes via approve_protected_change().

4. This safety protocol CANNOT be modified by any agent. self_coder.py
   is in the protected files list and cannot self-modify.

5. Privacy-related code (PII handling, data anonymization, DPDP compliance,
   phone number hashing, redaction) is ALWAYS protected.
───────────────────────────────────────────────────────────────────

Other Safety:
- ALL file operations restricted to ``backend/app/`` (no escaping via ../).
- AST validation before every Python file write.
- Maximum file size: 500KB.
- Secret files (.env, credentials) cannot be read or modified.
- Every applied change is logged in an in-memory change log.
- Proposals must pass validation before application.
- Rollback support for every applied change.

Usage::

    from app.services.self_coder import get_self_coder

    sc = get_self_coder()
    source = sc.read_source("agents/vikram.py")
    proposal = sc.propose_change(
        file_path="agents/vikram.py",
        old_code="default_complexity = TaskComplexity.HIGH",
        new_code="default_complexity = TaskComplexity.MEDIUM",
        reason="Reduce cost for architecture planning",
        proposed_by="tilotma",
    )
    result = sc.apply_change(proposal.id)
"""

from __future__ import annotations

import ast
import logging
import re
import textwrap
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

_MAX_FILE_SIZE_BYTES = 500 * 1024  # 500 KB
_MAX_SEARCH_RESULTS = 50

_VALID_CHANGE_TYPES = frozenset({
    "edit",
    "create",
    "add_tool",
    "modify_prompt",
    "create_agent",
})

_VALID_STATUSES = frozenset({
    "pending",
    "validated",
    "applied",
    "rolled_back",
    "rejected",
    "requires_human_review",  # Safety protocol: needs human engineer approval
})

# Files that must never be read or modified (secrets).
_FORBIDDEN_PATTERNS = frozenset({
    ".env",
    "credentials",
    "secrets",
    ".pem",
    ".key",
    "id_rsa",
    "id_ed25519",
})

# ── IMMUTABLE SAFETY PROTOCOL ────────────────────────────────────────
# AGENTS CANNOT MODIFY THESE DEFINITIONS. self_coder.py is itself
# protected, so an agent cannot remove or weaken these guards.

# Files that are COMPLETELY locked from agent modification.
# Changes to these files ALWAYS require human engineer approval.
_PROTECTED_FILES: frozenset[str] = frozenset({
    # Self-coder itself (prevents agents from weakening safety)
    "services/self_coder.py",
    # Secret management
    "services/secret_manager.py",
    # Security agents and services
    "agents/security_guardian.py",
    "agents/karan.py",          # Security auditor — cannot weaken audits
    "agents/attack_tester.py",  # Pen testing — cannot disable tests
    # Authentication and authorization
    "services/rate_limiter.py",
    # Privacy-critical (DPDP compliance)
    "agents/whatsapp_agent.py",  # Contains PII handling / phone redaction
})

# Code patterns that are PROTECTED everywhere — if a proposed change
# modifies code matching ANY of these patterns, it requires human review.
# These are checked in BOTH old_code AND new_code of every proposal.
_PROTECTED_CODE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Authentication / authorization
    re.compile(r"verify_password|hash_password|check_password", re.IGNORECASE),
    re.compile(r"jwt_secret|jwt_token|access_token|refresh_token", re.IGNORECASE),
    re.compile(r"Bearer\s|Authorization|authenticate|authorize", re.IGNORECASE),
    re.compile(r"hmac\.|hashlib\.|bcrypt\.|passlib\.", re.IGNORECASE),
    # Rate limiting / abuse prevention
    re.compile(r"rate_limit|throttle|circuit_breaker", re.IGNORECASE),
    # Encryption / key management
    re.compile(r"encrypt|decrypt|Fernet|AES|RSA|private_key|public_key", re.IGNORECASE),
    re.compile(r"SECRET_KEY|API_KEY|api_key|secret_key", re.IGNORECASE),
    # Privacy / PII handling
    re.compile(r"redact_phone|_hash_phone|anonymize|mask_pii|dpdp", re.IGNORECASE),
    re.compile(r"phone_number|email.*redact|pii_|personal_data", re.IGNORECASE),
    # Safety protocol markers (self-referential protection)
    re.compile(r"_PROTECTED_FILES|_PROTECTED_CODE_PATTERNS|SAFETY PROTOCOL", re.IGNORECASE),
    re.compile(r"requires_human_review|approve_protected_change", re.IGNORECASE),
    re.compile(r"_check_safety_protocol|_FORBIDDEN_PATTERNS", re.IGNORECASE),
    # Webhook signature verification
    re.compile(r"verify_webhook_signature|hmac\.compare_digest", re.IGNORECASE),
    # SQL injection prevention
    re.compile(r"parameterized|sql_injection|sanitize.*input", re.IGNORECASE),
)

# ── END IMMUTABLE SAFETY PROTOCOL ────────────────────────────────────


# ── Fuzzy Find-and-Replace Helper ────────────────────────────────────


def _find_and_replace(current: str, old_code: str, new_code: str) -> str | None:
    """3-tier matching: exact → normalized → fuzzy. Returns patched code or None.

    Tier 1: Exact substring match (existing behavior).
    Tier 2: Normalized match — strip trailing whitespace per line.
    Tier 3: Fuzzy match — difflib SequenceMatcher ≥ 0.85 ratio.
    """
    # Tier 1: Exact match (existing behavior — fastest)
    if old_code in current:
        return current.replace(old_code, new_code, 1)

    # Tier 2: Normalized match (handles trailing whitespace / line ending diffs)
    def _normalize(s: str) -> str:
        return "\n".join(line.rstrip() for line in s.splitlines())

    norm_current = _normalize(current)
    norm_old = _normalize(old_code)
    if norm_old and norm_old in norm_current:
        start = norm_current.index(norm_old)
        line_start = norm_current[:start].count("\n")
        old_line_count = norm_old.count("\n") + 1
        original_lines = current.splitlines(keepends=True)
        prefix = "".join(original_lines[:line_start])
        suffix = "".join(original_lines[line_start + old_line_count:])
        return prefix + new_code + suffix

    # Tier 3: Fuzzy match (handles minor reformatting / indentation shifts)
    import difflib
    current_lines = current.splitlines(keepends=True)
    old_lines = old_code.splitlines()
    if not old_lines:
        return None
    best_ratio, best_start, best_end = 0.0, 0, 0
    window_min = max(1, len(old_lines) - 2)
    window_max = len(old_lines) + 3
    for i in range(len(current_lines)):
        for j in range(i + window_min, min(i + window_max, len(current_lines) + 1)):
            candidate = "".join(current_lines[i:j])
            ratio = difflib.SequenceMatcher(
                None, _normalize(candidate), norm_old,
            ).ratio()
            if ratio > best_ratio:
                best_ratio, best_start, best_end = ratio, i, j
    if best_ratio >= 0.85:
        prefix = "".join(current_lines[:best_start])
        suffix = "".join(current_lines[best_end:])
        return prefix + new_code + suffix

    return None  # No match found at any tier


# ── ChangeProposal Dataclass ─────────────────────────────────────────


@dataclass
class ChangeProposal:
    """A proposed source-code change created by an agent.

    Lifecycle:
        pending -> validated -> applied -> (optionally) rolled_back
        pending -> rejected  (if validation fails)
    """

    id: str
    file_path: str          # Relative to backend/
    change_type: str        # edit | create | add_tool | modify_prompt | create_agent
    old_code: str           # Empty for creates
    new_code: str
    reason: str             # Why the agent wants this change
    proposed_by: str        # Agent name
    status: str = "pending"
    validation_errors: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # SAFETY PROTOCOL: True when change touches protected code.
    # When True, the proposal CANNOT be applied without human approval.
    requires_human_review: bool = False
    human_approved_by: str = ""  # Engineer who approved (empty = not approved)
    safety_flags: list[str] = field(default_factory=list)  # Which safety rules triggered

    # Internal: stores the full old file content for rollback.
    _old_file_content: str = field(default="", repr=False)


# ── SelfCoder Service ────────────────────────────────────────────────


class SelfCoder:
    """Core service for agent self-modification of NexSidi source code.

    All paths are relative to ``backend/app/`` unless otherwise noted.
    The ``_base_dir`` is resolved from this module's location:
    ``Path(__file__).resolve().parent.parent`` which yields ``backend/app``.
    """

    def __init__(self) -> None:
        self._base_dir: Path = Path(__file__).resolve().parent.parent
        self._proposals: dict[str, ChangeProposal] = {}
        self._change_log: list[dict[str, Any]] = []
        # SAFETY PROTOCOL: Track human approvals for protected changes.
        # Only approve_protected_change() can add entries here.
        self._human_approvals: dict[str, str] = {}  # proposal_id -> approved_by

    # ── SAFETY PROTOCOL ENFORCEMENT ──────────────────────────────────

    def _check_safety_protocol(self, proposal: ChangeProposal) -> list[str]:
        """Check if a proposal touches protected safety/security/privacy code.

        Returns a list of safety flags (reasons why human review is needed).
        Empty list = safe for agent to apply autonomously.

        THIS METHOD IS PART OF THE IMMUTABLE SAFETY PROTOCOL.
        Agents cannot modify this method because self_coder.py is protected.
        """
        flags: list[str] = []

        # Check 1: Is the target file in the protected list?
        clean_path = proposal.file_path.replace("\\", "/").lstrip("/")
        if clean_path.startswith("app/"):
            clean_path = clean_path[4:]
        for protected in _PROTECTED_FILES:
            if clean_path == protected or clean_path.endswith(protected):
                flags.append(f"PROTECTED_FILE: {protected}")

        # Check 2: Does the change modify protected code patterns?
        combined_code = f"{proposal.old_code}\n{proposal.new_code}"
        for pattern in _PROTECTED_CODE_PATTERNS:
            match = pattern.search(combined_code)
            if match:
                flags.append(f"PROTECTED_PATTERN: {match.group()}")

        # Check 3: Is the agent trying to modify safety-related constants?
        if any(kw in proposal.new_code for kw in (
            "_PROTECTED_FILES", "_PROTECTED_CODE_PATTERNS",
            "_FORBIDDEN_PATTERNS", "requires_human_review",
            "approve_protected_change", "_check_safety_protocol",
        )):
            flags.append("SAFETY_SELF_MODIFICATION: attempting to change safety protocol")

        return flags

    def approve_protected_change(
        self,
        proposal_id: str,
        approved_by: str,
    ) -> dict[str, Any]:
        """Human engineer approves a protected change.

        ONLY callable by human engineers via API/CLI. Agents CANNOT call this
        because:
        1. There is no tool definition for this method
        2. The safety protocol check would flag any attempt to add one
        3. self_coder.py is in _PROTECTED_FILES

        Args:
            proposal_id: The proposal to approve.
            approved_by: Name/email of the human engineer approving.

        Returns:
            Result dict with success/error.
        """
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            return {"success": False, "error": f"Proposal not found: {proposal_id}"}

        if proposal.status != "requires_human_review":
            return {
                "success": False,
                "error": f"Proposal status is '{proposal.status}', expected 'requires_human_review'",
            }

        if not approved_by or len(approved_by) < 2:
            return {"success": False, "error": "approved_by must be a real engineer identifier"}

        # Record approval
        proposal.human_approved_by = approved_by
        proposal.status = "validated"  # Now it can be applied
        self._human_approvals[proposal_id] = approved_by

        logger.info(
            "safety_protocol_human_approval: proposal=%s file=%s approved_by=%s flags=%s",
            proposal_id, proposal.file_path, approved_by, proposal.safety_flags[:3],
        )

        return {
            "success": True,
            "message": f"Proposal {proposal_id} approved by {approved_by}. "
                       f"It can now be applied via apply_change().",
        }

    def get_pending_reviews(self) -> list[dict[str, Any]]:
        """List all proposals awaiting human review.

        Returns a list of dicts with proposal details for display to engineers.
        """
        pending: list[dict[str, Any]] = []
        for p in self._proposals.values():
            if p.status == "requires_human_review":
                pending.append({
                    "proposal_id": p.id,
                    "file_path": p.file_path,
                    "change_type": p.change_type,
                    "proposed_by": p.proposed_by,
                    "reason": p.reason,
                    "safety_flags": p.safety_flags,
                    "created_at": p.created_at,
                    "old_code_preview": p.old_code[:200] if p.old_code else "(new file)",
                    "new_code_preview": p.new_code[:200],
                })
        return pending

    # ── Path Safety ──────────────────────────────────────────────────

    def _resolve_safe_path(self, file_path: str) -> Path | None:
        """Resolve *file_path* to an absolute path under ``_base_dir``.

        Returns ``None`` (and logs a warning) if the resolved path escapes
        ``backend/app/`` or refers to a forbidden secret file.
        """
        # Normalise separators and strip leading slashes / "app/" prefix
        clean = file_path.replace("\\", "/").lstrip("/")
        if clean.startswith("app/"):
            clean = clean[4:]

        resolved = (self._base_dir / clean).resolve()

        # Must remain within _base_dir
        try:
            resolved.relative_to(self._base_dir)
        except ValueError:
            logger.warning("self_coder_path_escape: file=%s resolved=%s", file_path, str(resolved))
            return None

        # Forbidden secret files
        name_lower = resolved.name.lower()
        for pattern in _FORBIDDEN_PATTERNS:
            if pattern in name_lower:
                logger.warning("self_coder_forbidden_file: file=%s pattern=%s", file_path, pattern)
                return None

        return resolved

    # ── Reading Source ────────────────────────────────────────────────

    def read_source(self, file_path: str) -> str:
        """Read any NexSidi source file.

        *file_path* is relative to ``backend/app/`` (e.g. ``"agents/vikram.py"``).
        Returns the file content as a string, or an error message prefixed
        with ``"ERROR:"``.
        """
        resolved = self._resolve_safe_path(file_path)
        if resolved is None:
            return f"ERROR: path not allowed: {file_path}"

        if not resolved.is_file():
            return f"ERROR: file not found: {file_path}"

        size = resolved.stat().st_size
        if size > _MAX_FILE_SIZE_BYTES:
            return f"ERROR: file too large ({size} bytes, max {_MAX_FILE_SIZE_BYTES})"

        try:
            return resolved.read_text(encoding="utf-8")
        except Exception as exc:
            logger.error("self_coder_read_error: file=%s error=%s", file_path, str(exc)[:200])
            return f"ERROR: could not read file: {exc}"

    # ── Listing Source Files ─────────────────────────────────────────

    def list_source_files(self, pattern: str = "**/*.py") -> list[str]:
        """List source files matching *pattern* under ``backend/app/``.

        Returns paths relative to ``backend/app/`` (forward-slash separated).
        """
        results: list[str] = []
        for p in sorted(self._base_dir.glob(pattern)):
            if p.is_file():
                try:
                    rel = p.relative_to(self._base_dir)
                    rel_str = str(rel).replace("\\", "/")
                    # Skip forbidden files
                    if any(fp in rel_str.lower() for fp in _FORBIDDEN_PATTERNS):
                        continue
                    results.append(rel_str)
                except ValueError:
                    continue
        return results

    # ── Searching Source ──────────────────────────────────────────────

    def search_source(
        self,
        query: str,
        file_pattern: str = "*.py",
    ) -> list[dict[str, Any]]:
        """Search across source files for a regex *query*.

        Searches all files matching *file_pattern* under ``backend/app/``.
        Returns a list of ``{"file": str, "line": int, "content": str}`` dicts,
        capped at :data:`_MAX_SEARCH_RESULTS`.
        """
        try:
            regex = re.compile(query, re.IGNORECASE)
        except re.error as exc:
            logger.warning("self_coder_bad_regex: query=%s error=%s", query, str(exc)[:100])
            return [{"file": "(error)", "line": 0, "content": f"Invalid regex: {exc}"}]

        matches: list[dict[str, Any]] = []

        for p in sorted(self._base_dir.rglob(file_pattern)):
            if not p.is_file():
                continue
            if p.stat().st_size > _MAX_FILE_SIZE_BYTES:
                continue

            rel_str = str(p.relative_to(self._base_dir)).replace("\\", "/")
            if any(fp in rel_str.lower() for fp in _FORBIDDEN_PATTERNS):
                continue

            try:
                content = p.read_text(encoding="utf-8")
            except Exception:
                continue

            for line_num, line in enumerate(content.splitlines(), start=1):
                if regex.search(line):
                    matches.append({
                        "file": rel_str,
                        "line": line_num,
                        "content": line.rstrip()[:200],
                    })
                    if len(matches) >= _MAX_SEARCH_RESULTS:
                        return matches

        return matches

    # ── Proposing Changes ────────────────────────────────────────────

    def propose_change(
        self,
        file_path: str,
        old_code: str,
        new_code: str,
        reason: str,
        proposed_by: str,
        change_type: str = "edit",
    ) -> ChangeProposal:
        """Create and validate a change proposal.

        Validations:
        - File path is within allowed directories.
        - AST parse of *new_code* succeeds (for ``.py`` files, if the
          change is a full-file create).
        - *old_code* actually exists in the current file (for edits).
        - *new_code* differs from *old_code*.

        The proposal is stored in ``_proposals`` regardless of validation
        outcome; callers should check ``proposal.status`` and
        ``proposal.validation_errors`` before applying.
        """
        proposal_id = str(uuid.uuid4())

        if change_type not in _VALID_CHANGE_TYPES:
            change_type = "edit"

        proposal = ChangeProposal(
            id=proposal_id,
            file_path=file_path,
            change_type=change_type,
            old_code=old_code,
            new_code=new_code,
            reason=reason,
            proposed_by=proposed_by,
        )

        errors = self._validate_proposal(proposal)
        proposal.validation_errors = errors

        # SAFETY PROTOCOL: Check if this touches protected code
        safety_flags = self._check_safety_protocol(proposal)
        proposal.safety_flags = safety_flags
        if safety_flags:
            proposal.requires_human_review = True

        if errors:
            proposal.status = "rejected"
            logger.warning(
                "self_coder_proposal_rejected: id=%s errors=%s by=%s",
                proposal_id, errors[:3], proposed_by,
            )
        elif safety_flags:
            # LOCKED: Needs human engineer approval
            proposal.status = "requires_human_review"
            logger.warning(
                "self_coder_safety_protocol_triggered: proposal=%s file=%s by=%s flags=%s "
                "BLOCKED: requires human engineer approval via approve_protected_change()",
                proposal_id, file_path, proposed_by, safety_flags[:3],
            )
        else:
            proposal.status = "validated"
            logger.info(
                "self_coder_proposal_validated: id=%s file=%s type=%s by=%s",
                proposal_id, file_path, change_type, proposed_by,
            )

        self._proposals[proposal_id] = proposal
        return proposal

    def _validate_proposal(self, proposal: ChangeProposal) -> list[str]:
        """Run all validation checks on a proposal.  Returns list of errors."""
        errors: list[str] = []

        # 1. Path safety
        resolved = self._resolve_safe_path(proposal.file_path)
        if resolved is None:
            errors.append(f"File path not allowed: {proposal.file_path}")
            return errors  # No further checks possible

        # 2. For edits, old_code must exist in the current file
        if proposal.change_type == "edit":
            if not resolved.is_file():
                errors.append(f"File does not exist (cannot edit): {proposal.file_path}")
                return errors
            try:
                current = resolved.read_text(encoding="utf-8")
            except Exception as exc:
                errors.append(f"Cannot read file: {exc}")
                return errors

            patched = _find_and_replace(current, proposal.old_code, proposal.new_code)
            if patched is None:
                errors.append(
                    "old_code not found in current file content. "
                    "Ensure the exact text (including whitespace) matches."
                )

        # 3. new_code must differ from old_code
        if proposal.new_code == proposal.old_code:
            errors.append("new_code is identical to old_code -- no change proposed.")

        # 4. For creates, file must NOT already exist
        if proposal.change_type in ("create", "create_agent") and resolved.is_file():
            errors.append(f"File already exists (use 'edit' instead): {proposal.file_path}")

        # 5. AST validation for Python files
        if proposal.file_path.endswith(".py"):
            if proposal.change_type in ("create", "create_agent"):
                # Validate the full new file
                ast_err = self._ast_check(proposal.new_code, proposal.file_path)
                if ast_err:
                    errors.append(ast_err)
            elif proposal.change_type == "edit" and not errors:
                # Validate the file AFTER the edit would be applied
                # (patched was already computed by _find_and_replace above)
                try:
                    ast_err = self._ast_check(patched, proposal.file_path)
                    if ast_err:
                        errors.append(f"Patched file fails AST: {ast_err}")
                except Exception:
                    pass  # Already caught above

        # 6. File size guard
        if len(proposal.new_code.encode("utf-8")) > _MAX_FILE_SIZE_BYTES:
            errors.append(
                f"new_code exceeds max file size "
                f"({len(proposal.new_code.encode('utf-8'))} > {_MAX_FILE_SIZE_BYTES})"
            )

        return errors

    @staticmethod
    def _ast_check(code: str, filename: str = "<proposal>") -> str | None:
        """Try to AST-parse *code*.  Returns error string or ``None``."""
        try:
            ast.parse(code, filename=filename)
            return None
        except SyntaxError as exc:
            return f"SyntaxError at line {exc.lineno}: {exc.msg}"

    # ── Applying Changes ─────────────────────────────────────────────

    def apply_change(self, proposal_id: str) -> dict[str, Any]:
        """Apply a validated proposal to the filesystem.

        Returns a result dict with keys ``success``, ``message``, and
        optionally ``error``.
        """
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            return {"success": False, "error": f"Proposal not found: {proposal_id}"}

        if proposal.status == "requires_human_review":
            return {
                "success": False,
                "error": (
                    "SAFETY PROTOCOL: This change touches protected code "
                    f"(flags: {proposal.safety_flags}). "
                    "A human engineer must approve via approve_protected_change() "
                    "before this change can be applied. "
                    "Use get_pending_reviews() to see all blocked proposals."
                ),
            }

        if proposal.status not in ("validated",):
            return {
                "success": False,
                "error": (
                    f"Proposal status is '{proposal.status}' -- "
                    "only 'validated' proposals can be applied."
                ),
            }

        resolved = self._resolve_safe_path(proposal.file_path)
        if resolved is None:
            return {"success": False, "error": f"Path not allowed: {proposal.file_path}"}

        try:
            if proposal.change_type in ("create", "create_agent"):
                # Store empty string as old content (file didn't exist)
                proposal._old_file_content = ""

                # Ensure parent directory exists
                resolved.parent.mkdir(parents=True, exist_ok=True)

                # AST validate the full file
                if proposal.file_path.endswith(".py"):
                    ast_err = self._ast_check(proposal.new_code, proposal.file_path)
                    if ast_err:
                        proposal.status = "rejected"
                        return {"success": False, "error": f"AST validation failed: {ast_err}"}

                resolved.write_text(proposal.new_code, encoding="utf-8")

            elif proposal.change_type in ("edit", "add_tool", "modify_prompt"):
                if not resolved.is_file():
                    return {"success": False, "error": f"File not found: {proposal.file_path}"}

                current = resolved.read_text(encoding="utf-8")
                proposal._old_file_content = current

                if proposal.old_code:
                    patched = _find_and_replace(current, proposal.old_code, proposal.new_code)
                    if patched is None:
                        proposal.status = "rejected"
                        return {
                            "success": False,
                            "error": "old_code no longer found in file (concurrent modification?)",
                        }
                else:
                    # Append mode (old_code is empty for add_tool / modify_prompt appends)
                    patched = current + "\n" + proposal.new_code

                # AST validate the patched result
                if proposal.file_path.endswith(".py"):
                    ast_err = self._ast_check(patched, proposal.file_path)
                    if ast_err:
                        proposal.status = "rejected"
                        return {"success": False, "error": f"Patched file AST error: {ast_err}"}

                # Size guard
                if len(patched.encode("utf-8")) > _MAX_FILE_SIZE_BYTES:
                    proposal.status = "rejected"
                    return {"success": False, "error": "Patched file exceeds max size (500KB)"}

                resolved.write_text(patched, encoding="utf-8")

            else:
                return {"success": False, "error": f"Unsupported change_type: {proposal.change_type}"}

            # Mark applied and log
            proposal.status = "applied"
            log_entry = {
                "proposal_id": proposal.id,
                "file_path": proposal.file_path,
                "change_type": proposal.change_type,
                "proposed_by": proposal.proposed_by,
                "reason": proposal.reason,
                "applied_at": datetime.now(timezone.utc).isoformat(),
            }
            self._change_log.append(log_entry)

            logger.info(
                "self_coder_change_applied: id=%s file=%s type=%s by=%s",
                proposal.id, proposal.file_path, proposal.change_type, proposal.proposed_by,
            )

            return {"success": True, "message": f"Change applied to {proposal.file_path}"}

        except Exception as exc:
            logger.error(
                "self_coder_apply_error: id=%s error=%s",
                proposal.id, str(exc)[:300],
            )
            return {"success": False, "error": f"Apply failed: {exc}"}

    # ── Rollback ─────────────────────────────────────────────────────

    def rollback_change(self, proposal_id: str) -> dict[str, Any]:
        """Rollback a previously applied change.

        Restores the file to its state before the proposal was applied.
        """
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            return {"success": False, "error": f"Proposal not found: {proposal_id}"}

        if proposal.status != "applied":
            return {
                "success": False,
                "error": (
                    f"Proposal status is '{proposal.status}' -- "
                    "only 'applied' proposals can be rolled back."
                ),
            }

        resolved = self._resolve_safe_path(proposal.file_path)
        if resolved is None:
            return {"success": False, "error": f"Path not allowed: {proposal.file_path}"}

        try:
            if proposal.change_type in ("create", "create_agent"):
                # File was created -- delete it to rollback
                if resolved.is_file():
                    resolved.unlink()
            else:
                # Restore original content
                if not proposal._old_file_content and proposal._old_file_content != "":
                    return {
                        "success": False,
                        "error": "No old file content stored -- cannot rollback.",
                    }
                resolved.write_text(proposal._old_file_content, encoding="utf-8")

            proposal.status = "rolled_back"
            self._change_log.append({
                "proposal_id": proposal.id,
                "file_path": proposal.file_path,
                "change_type": "rollback",
                "proposed_by": proposal.proposed_by,
                "reason": f"Rollback of {proposal.change_type}",
                "applied_at": datetime.now(timezone.utc).isoformat(),
            })

            logger.info(
                "self_coder_change_rolled_back: id=%s file=%s",
                proposal.id, proposal.file_path,
            )

            return {"success": True, "message": f"Rolled back change to {proposal.file_path}"}

        except Exception as exc:
            logger.error(
                "self_coder_rollback_error: id=%s error=%s",
                proposal.id, str(exc)[:300],
            )
            return {"success": False, "error": f"Rollback failed: {exc}"}

    # ── High-Level: Create Tool for Agent ────────────────────────────

    def create_tool_for_agent(
        self,
        agent_name: str,
        tool_name: str,
        tool_description: str,
        tool_parameters: dict[str, Any],
        handler_code: str,
        reason: str,
    ) -> ChangeProposal:
        """Add a new tool to an existing agent.

        Locates the agent's source file, finds the tool registration
        section (``register_tool(ToolDefinition(...))``) and the handler
        method block, then creates a combined proposal to inject both
        the ToolDefinition registration and the handler method.

        Args:
            agent_name: The agent's ``name`` attribute (e.g. ``"vikram"``).
            tool_name: Name for the new tool.
            tool_description: Tool description shown to the AI.
            tool_parameters: JSON Schema dict for tool parameters.
            handler_code: Python source for the handler method (async def).
            reason: Why this tool is being added.

        Returns:
            A ``ChangeProposal`` (check ``.status`` and ``.validation_errors``).
        """
        # Find the agent's source file
        agent_file = self._find_agent_file(agent_name)
        if agent_file is None:
            proposal = ChangeProposal(
                id=str(uuid.uuid4()),
                file_path=f"agents/{agent_name}.py",
                change_type="add_tool",
                old_code="",
                new_code="",
                reason=reason,
                proposed_by=agent_name,
                status="rejected",
                validation_errors=[f"Agent file not found for: {agent_name}"],
            )
            self._proposals[proposal.id] = proposal
            return proposal

        # Read current source
        source = self.read_source(agent_file)
        if source.startswith("ERROR:"):
            proposal = ChangeProposal(
                id=str(uuid.uuid4()),
                file_path=agent_file,
                change_type="add_tool",
                old_code="",
                new_code="",
                reason=reason,
                proposed_by=agent_name,
                status="rejected",
                validation_errors=[source],
            )
            self._proposals[proposal.id] = proposal
            return proposal

        # Find the last register_tool call to insert after it
        import json as _json

        params_str = _json.dumps(tool_parameters, indent=12)

        tool_registration = textwrap.dedent(f"""\

        self.register_tool(ToolDefinition(
            name="{tool_name}",
            description={tool_description!r},
            parameters={params_str},
        ))
""")

        # Find the anchor: last occurrence of "self.register_tool(ToolDefinition("
        anchor_pattern = r"self\.register_tool\(ToolDefinition\("
        matches = list(re.finditer(anchor_pattern, source))
        if not matches:
            return self.propose_change(
                file_path=agent_file,
                old_code="",
                new_code=tool_registration + "\n" + handler_code,
                reason=reason,
                proposed_by=agent_name,
                change_type="add_tool",
            )

        # Find the end of the last register_tool block (matching closing paren)
        last_match = matches[-1]
        # Walk forward to find the closing "))" for ToolDefinition registration
        paren_depth = 0
        end_pos = last_match.start()
        for i in range(last_match.start(), len(source)):
            if source[i] == "(":
                paren_depth += 1
            elif source[i] == ")":
                paren_depth -= 1
                if paren_depth == 0:
                    end_pos = i + 1
                    break

        # The old_code is the last register_tool call (we insert after it)
        old_block = source[last_match.start():end_pos]

        # Build new_code: original block + new registration
        new_block = old_block + "\n" + tool_registration

        # Now handle the handler method -- append it before the class ends
        # We add the handler as a new proposal for the same file, but we
        # can combine both into one proposal by also appending handler_code.
        # Find the best spot: just before the last method in the class.
        # Simplified approach: append handler after the tool registration.

        return self.propose_change(
            file_path=agent_file,
            old_code=old_block,
            new_code=new_block + "\n" + textwrap.dedent(handler_code),
            reason=reason,
            proposed_by=agent_name,
            change_type="add_tool",
        )

    # ── High-Level: Modify Agent Prompt ──────────────────────────────

    def modify_agent_prompt(
        self,
        agent_name: str,
        prompt_section: str,
        new_content: str,
        reason: str,
    ) -> ChangeProposal:
        """Modify an agent's system prompt or instructions.

        Finds the string *prompt_section* in the agent's source file and
        replaces it with *new_content*.

        Args:
            agent_name: Agent ``name`` attribute.
            prompt_section: The exact current prompt text to replace.
            new_content: The new prompt text.
            reason: Why the prompt is being changed.

        Returns:
            A ``ChangeProposal``.
        """
        agent_file = self._find_agent_file(agent_name)
        if agent_file is None:
            proposal = ChangeProposal(
                id=str(uuid.uuid4()),
                file_path=f"agents/{agent_name}.py",
                change_type="modify_prompt",
                old_code=prompt_section,
                new_code=new_content,
                reason=reason,
                proposed_by=agent_name,
                status="rejected",
                validation_errors=[f"Agent file not found for: {agent_name}"],
            )
            self._proposals[proposal.id] = proposal
            return proposal

        return self.propose_change(
            file_path=agent_file,
            old_code=prompt_section,
            new_code=new_content,
            reason=reason,
            proposed_by=agent_name,
            change_type="modify_prompt",
        )

    # ── High-Level: Create New Agent ─────────────────────────────────

    def create_new_agent(
        self,
        name: str,
        display_name: str,
        description: str,
        tools: list[dict[str, Any]],
        execute_logic: str,
        reason: str,
    ) -> ChangeProposal:
        """Create an entirely new agent file from a template.

        Generates a complete Python module following the NexSidi agent
        pattern (standalone class with ``register_tool``, ``execute``,
        and module-level ``register_agent()`` call).

        Args:
            name: Agent ``name`` attribute (e.g. ``"maya"``).
            display_name: Human-readable name (e.g. ``"Maya -- API Designer"``).
            description: Module docstring for the agent.
            tools: List of tool definitions, each with keys ``name``,
                   ``description``, ``parameters``.
            execute_logic: Python source code for the ``execute()`` method body
                           (indented at 8 spaces).
            reason: Why this agent is being created.

        Returns:
            A ``ChangeProposal`` for the new agent file.
        """
        import json as _json

        # Build tool registration code
        tool_registrations: list[str] = []
        for tool in tools:
            t_name = tool.get("name", "unnamed_tool")
            t_desc = tool.get("description", "")
            t_params = _json.dumps(tool.get("parameters", {}), indent=16)
            tool_registrations.append(textwrap.dedent(f"""\
        self.register_tool(ToolDefinition(
            name="{t_name}",
            description={t_desc!r},
            parameters={t_params},
        ))"""))

        tools_block = "\n\n".join(tool_registrations) if tool_registrations else "        pass  # No tools registered yet"

        # Build the full agent module source
        agent_source = textwrap.dedent(f'''\
"""{display_name}: {description}

Auto-generated by SelfCoder. Reason: {reason}
"""

from __future__ import annotations

from typing import Any

import structlog

from app.agents.base import (
    AgentResult,
    AgentStatus,
    ToolDefinition,
    call_ai,
    call_ai_with_tools,
    handle_web_tool,
    register_agent,
    run_agent,
    store_output,
)
from app.services.ai_router import TaskComplexity

logger = structlog.get_logger(__name__)


class {name.capitalize()}:
    """{display_name}.

    {description}
    """

    name = "{name}"
    display_name = "{display_name}"
    default_complexity = TaskComplexity.MEDIUM
    default_model: str | None = None

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {{}}

{tools_block}

    def register_tool(self, tool: ToolDefinition) -> None:
        """Register a tool available to this agent."""
        self._tools[tool.name] = tool

    @property
    def tools(self) -> list[ToolDefinition]:
        """All registered tools."""
        return list(self._tools.values())

    async def run(
        self,
        pipeline_run_id: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Execute with timing, logging, and error handling."""
        return await run_agent(self, pipeline_run_id, context)

    async def execute(
        self,
        pipeline_run_id: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Main execution logic for {name}."""
{execute_logic}


# Register the agent
_{name} = {name.capitalize()}()
register_agent(_{name})
''')

        return self.propose_change(
            file_path=f"agents/{name}.py",
            old_code="",
            new_code=agent_source,
            reason=reason,
            proposed_by=name,
            change_type="create_agent",
        )

    # ── Retrieval ────────────────────────────────────────────────────

    def get_change_log(self) -> list[dict[str, Any]]:
        """Return the history of all applied (and rolled-back) changes."""
        return list(self._change_log)

    def get_proposal(self, proposal_id: str) -> ChangeProposal | None:
        """Get a specific proposal by ID."""
        return self._proposals.get(proposal_id)

    # ── Agent File Discovery ─────────────────────────────────────────

    def _find_agent_file(self, agent_name: str) -> str | None:
        """Find the source file for an agent by its ``name`` attribute.

        Searches ``backend/app/agents/*.py`` for a class with
        ``name = "agent_name"``.

        Returns the path relative to ``backend/app/`` (e.g.
        ``"agents/vikram.py"``) or ``None``.
        """
        agents_dir = self._base_dir / "agents"
        if not agents_dir.is_dir():
            return None

        # Quick check: try the obvious filename first
        obvious = agents_dir / f"{agent_name}.py"
        if obvious.is_file():
            try:
                content = obvious.read_text(encoding="utf-8")
                if re.search(rf'''name\s*=\s*["']{re.escape(agent_name)}["']''', content):
                    return f"agents/{agent_name}.py"
            except Exception:
                pass

        # Fallback: scan all agent files
        for p in agents_dir.glob("*.py"):
            if p.name.startswith("_"):
                continue
            try:
                content = p.read_text(encoding="utf-8")
                if re.search(rf'''name\s*=\s*["']{re.escape(agent_name)}["']''', content):
                    return f"agents/{p.name}"
            except Exception:
                continue

        return None


# ── Singleton ────────────────────────────────────────────────────────

_self_coder_instance: SelfCoder | None = None


def get_self_coder() -> SelfCoder:
    """Return the singleton ``SelfCoder`` instance."""
    global _self_coder_instance
    if _self_coder_instance is None:
        _self_coder_instance = SelfCoder()
    return _self_coder_instance
