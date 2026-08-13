"""Atomic Bababucks balance mutations over BabaBot's legacy bank mapping."""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping
from threading import RLock
from typing import TypeAlias


BankEntry: TypeAlias = tuple[int, bool]
_MISSING = object()


class InsufficientFunds(ValueError):
    """Raised when a transaction would make an account negative."""

    def __init__(self, user_id: int, balance: int, debit: int):
        self.user_id = user_id
        self.balance = balance
        self.debit = debit
        super().__init__(
            f"account {user_id} has {balance} Bababucks; {debit} required"
        )


class BababucksLedger:
    """Serialize, persist, and roll back mutations to the shared bank mapping."""

    def __init__(
        self,
        bank: MutableMapping[int, BankEntry],
        lock: RLock,
        persist: Callable[[], None],
    ) -> None:
        self._bank = bank
        self._lock = lock
        self._persist = persist

    @staticmethod
    def _user_id(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("user_id must be an integer")
        return value

    @staticmethod
    def _amount(value: object, *, allow_negative: bool = True) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("amount must be an integer")
        if value == 0:
            raise ValueError("amount must not be zero")
        if not allow_negative and value < 0:
            raise ValueError("amount must be positive")
        return value

    def get_balance(self, user_id: int) -> int:
        uid = self._user_id(user_id)
        with self._lock:
            return int(self._bank.get(uid, (0, False))[0])

    def _normalize_deltas(
        self, deltas: Mapping[int, int], *, allow_empty: bool = False
    ) -> dict[int, int]:
        if not isinstance(deltas, Mapping):
            raise TypeError("deltas must be a mapping")
        if not deltas and not allow_empty:
            raise ValueError("transaction must contain at least one delta")

        normalized: dict[int, int] = {}
        for raw_uid, raw_delta in deltas.items():
            uid = self._user_id(raw_uid)
            delta = self._amount(raw_delta)
            normalized[uid] = normalized.get(uid, 0) + delta

        if any(delta == 0 for delta in normalized.values()):
            raise ValueError("net transaction amounts must not be zero")
        return normalized

    def _apply_locked(
        self,
        normalized: Mapping[int, int],
        state: MutableMapping[str, object] | None = None,
        state_updates: Mapping[str, object] | None = None,
    ) -> dict[int, int]:
        previous: dict[int, BankEntry | object] = {
            uid: self._bank.get(uid, _MISSING) for uid in normalized
        }
        previous_state = dict(state) if state is not None else None
        updated: dict[int, BankEntry] = {}

        for uid, delta in normalized.items():
            balance, claimed = self._bank.get(uid, (0, False))
            new_balance = int(balance) + delta
            if new_balance < 0:
                raise InsufficientFunds(uid, int(balance), -delta)
            updated[uid] = (new_balance, bool(claimed))

        self._bank.update(updated)
        try:
            if state is not None and state_updates is not None:
                state.update(state_updates)
            self._persist()
        except Exception:
            for uid, old_entry in previous.items():
                if old_entry is _MISSING:
                    self._bank.pop(uid, None)
                else:
                    self._bank[uid] = old_entry  # type: ignore[assignment]
            if state is not None and previous_state is not None:
                state.clear()
                state.update(previous_state)
            raise

        return {uid: entry[0] for uid, entry in updated.items()}

    def apply(self, deltas: Mapping[int, int]) -> dict[int, int]:
        """Apply all deltas and persist once, or leave every account unchanged."""
        normalized = self._normalize_deltas(deltas)
        with self._lock:
            return self._apply_locked(normalized)

    def apply_with_state(
        self,
        deltas: Mapping[int, int],
        state: MutableMapping[str, object],
        state_updates: Mapping[str, object],
    ) -> dict[int, int]:
        """Commit balance deltas and metadata through the same persistence call."""
        if not isinstance(state, MutableMapping):
            raise TypeError("state must be a mutable mapping")
        if not isinstance(state_updates, Mapping):
            raise TypeError("state_updates must be a mapping")
        normalized = self._normalize_deltas(deltas, allow_empty=True)
        with self._lock:
            return self._apply_locked(normalized, state, state_updates)

    def try_debit(self, user_id: int, amount: int) -> bool:
        uid = self._user_id(user_id)
        debit = self._amount(amount, allow_negative=False)
        try:
            self.apply({uid: -debit})
        except InsufficientFunds:
            return False
        return True

    def credit(self, user_id: int, amount: int) -> int:
        uid = self._user_id(user_id)
        credit = self._amount(amount, allow_negative=False)
        return self.apply({uid: credit})[uid]

    def adjust_clamped(self, user_id: int, amount: int) -> int:
        """Preserve legacy add_money behavior by clamping the balance at zero."""
        uid = self._user_id(user_id)
        if isinstance(amount, bool) or not isinstance(amount, int):
            raise TypeError("amount must be an integer")
        with self._lock:
            balance = int(self._bank.get(uid, (0, False))[0])
            new_balance = max(0, balance + amount)
            if new_balance == balance:
                return balance
            return self.apply({uid: new_balance - balance})[uid]


class BababucksEscrow:
    """Track game contributions only after their bank debit commits."""

    def __init__(
        self,
        ledger: BababucksLedger,
        *,
        state: MutableMapping[str, object] | None = None,
        escrow_id: str | None = None,
        game_type: str | None = None,
    ) -> None:
        self._ledger = ledger
        self._lock = RLock()
        self._contributions: dict[int, int] = {}
        self._state = "open"
        self._durable_state = state
        self._escrow_id = escrow_id
        self._game_type = game_type

        durable_args = (state, escrow_id, game_type)
        if any(value is not None for value in durable_args):
            if state is None or not isinstance(state, MutableMapping):
                raise TypeError("state must be a mutable mapping")
            if not isinstance(escrow_id, str) or not escrow_id.strip():
                raise ValueError("escrow_id must not be empty")
            if not isinstance(game_type, str) or not game_type.strip():
                raise ValueError("game_type must not be empty")
            records = state.get("casino_escrows", {})
            if not isinstance(records, Mapping):
                raise TypeError("casino_escrows must be a mapping")
            record = records.get(escrow_id)
            if record is not None:
                if not isinstance(record, Mapping):
                    raise TypeError("durable escrow record must be a mapping")
                if record.get("game_type") != game_type:
                    raise ValueError("escrow game type does not match")
                stored_state = record.get("state", "open")
                if stored_state not in {"open", "refunded", "settled"}:
                    raise ValueError("invalid durable escrow state")
                stored_contributions = record.get("contributions", {})
                if not isinstance(stored_contributions, Mapping):
                    raise TypeError("escrow contributions must be a mapping")
                self._state = str(stored_state)
                self._contributions = {
                    int(uid): int(amount)
                    for uid, amount in stored_contributions.items()
                }

    def _durable_updates(
        self, contributions: Mapping[int, int], state: str
    ) -> dict[str, object]:
        if self._durable_state is None or self._escrow_id is None:
            return {}
        current = self._durable_state.get("casino_escrows", {})
        if not isinstance(current, Mapping):
            raise TypeError("casino_escrows must be a mapping")
        records = dict(current)
        records[self._escrow_id] = {
            "game_type": self._game_type,
            "state": state,
            "contributions": {
                str(uid): int(amount)
                for uid, amount in contributions.items()
            },
        }
        return {"casino_escrows": records}

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def contributions(self) -> dict[int, int]:
        with self._lock:
            return dict(self._contributions)

    @property
    def total(self) -> int:
        with self._lock:
            return sum(self._contributions.values())

    def try_debit(self, user_id: int, amount: int) -> bool:
        return self.try_debit_many({user_id: amount})

    def try_debit_many(self, debits: Mapping[int, int]) -> bool:
        if not isinstance(debits, Mapping):
            raise TypeError("debits must be a mapping")
        if not debits:
            raise ValueError("debits must not be empty")

        normalized: dict[int, int] = {}
        for raw_uid, raw_amount in debits.items():
            uid = self._ledger._user_id(raw_uid)
            amount = self._ledger._amount(raw_amount, allow_negative=False)
            normalized[uid] = normalized.get(uid, 0) + amount

        with self._lock:
            if self._state != "open":
                return False
            contributions = dict(self._contributions)
            for uid, amount in normalized.items():
                contributions[uid] = contributions.get(uid, 0) + amount
            try:
                deltas = {
                    uid: -amount for uid, amount in normalized.items()
                }
                if self._durable_state is None:
                    self._ledger.apply(deltas)
                else:
                    self._ledger.apply_with_state(
                        deltas,
                        self._durable_state,
                        self._durable_updates(contributions, "open"),
                    )
            except InsufficientFunds:
                return False
            self._contributions = contributions
            return True

    def refund(self) -> bool:
        with self._lock:
            if self._state != "open":
                return False
            if self._durable_state is not None:
                self._ledger.apply_with_state(
                    dict(self._contributions),
                    self._durable_state,
                    self._durable_updates(
                        self._contributions, "refunded"
                    ),
                )
            elif self._contributions:
                self._ledger.apply(dict(self._contributions))
            self._state = "refunded"
            return True

    def settle(self, payouts: Mapping[int, int]) -> bool:
        with self._lock:
            if self._state != "open":
                return False
            if not isinstance(payouts, Mapping):
                raise TypeError("payouts must be a mapping")
            if self._durable_state is not None:
                self._ledger.apply_with_state(
                    payouts,
                    self._durable_state,
                    self._durable_updates(
                        self._contributions, "settled"
                    ),
                )
            elif payouts:
                self._ledger.apply(payouts)
            self._state = "settled"
            return True
