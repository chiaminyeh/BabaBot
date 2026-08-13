import ast
import json
import logging
import os
import tempfile
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from bababucks import BababucksEscrow, BababucksLedger


ROOT = Path(__file__).resolve().parents[1]


def load_baba_class():
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    baba_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Baba"
    )
    module = ast.Module(body=[baba_node], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "DAILY_REWARD": 100,
        "BANK_FILE": "bank.json",
        "BANK_META_KEY": "__bababucks_meta__",
        "BababucksEscrow": BababucksEscrow,
        "BababucksLedger": BababucksLedger,
        "json": json,
        "logger": logging.getLogger(__name__),
        "os": os,
        "tempfile": tempfile,
        "threading": threading,
    }
    exec(compile(module, str(ROOT / "main.py"), "exec"), namespace)
    return namespace["Baba"]


class RecordingLedger:
    def __init__(self):
        self.calls = []

    def get_balance(self, user_id):
        self.calls.append(("get_balance", user_id))
        return 123

    def try_debit(self, user_id, amount):
        self.calls.append(("try_debit", user_id, amount))
        return True

    def credit(self, user_id, amount):
        self.calls.append(("credit", user_id, amount))
        return 456

    def apply(self, deltas):
        self.calls.append(("apply", deltas))
        return {int(uid): 789 for uid in deltas}

    def apply_with_state(self, deltas, state, state_updates):
        self.calls.append(("apply_with_state", deltas, state, state_updates))
        return {int(uid): 987 for uid in deltas}

    def adjust_clamped(self, user_id, amount):
        self.calls.append(("adjust_clamped", user_id, amount))
        return 0


class BababucksFacadeContractTests(unittest.TestCase):
    def setUp(self):
        baba_class = load_baba_class()
        self.baba = object.__new__(baba_class)
        self.baba.ledger = RecordingLedger()

    def test_explicit_money_operations_delegate_to_ledger(self):
        self.assertEqual(self.baba.get_money("7"), 123)
        self.assertTrue(self.baba.try_debit_money("7", "25"))
        self.assertEqual(self.baba.credit_money("7", "30"), 456)
        self.assertEqual(
            self.baba.apply_money_deltas({"7": -5, 8: 5}),
            {7: 789, 8: 789},
        )
        self.assertEqual(
            self.baba.ledger.calls,
            [
                ("get_balance", 7),
                ("try_debit", 7, 25),
                ("credit", 7, 30),
                ("apply", {7: -5, 8: 5}),
            ],
        )

    def test_legacy_add_money_uses_clamped_adjustment(self):
        self.assertEqual(self.baba.add_money("7", "-999"), 0)
        self.assertEqual(
            self.baba.ledger.calls,
            [("adjust_clamped", 7, -999)],
        )

    def test_new_escrow_uses_the_canonical_ledger(self):
        escrow = self.baba.new_escrow()

        self.assertIsInstance(escrow, BababucksEscrow)
        self.assertIs(escrow._ledger, self.baba.ledger)

    def test_new_durable_escrow_uses_economy_state(self):
        self.baba.economy_state = {"casino_escrows": {}}

        escrow = self.baba.new_escrow(
            escrow_id="poker:99:hand-1", game_type="poker"
        )

        self.assertIs(escrow._durable_state, self.baba.economy_state)
        self.assertEqual(escrow._escrow_id, "poker:99:hand-1")
        self.assertEqual(escrow._game_type, "poker")

    def test_stateful_money_operation_delegates_to_ledger(self):
        self.baba.economy_state = {"last_lottery_draw_id": "old"}

        result = self.baba.apply_money_deltas_with_state(
            {"7": "25"}, {"last_lottery_draw_id": "new"}
        )

        self.assertEqual(result, {7: 987})
        self.assertEqual(
            self.baba.ledger.calls,
            [
                (
                    "apply_with_state",
                    {7: 25},
                    self.baba.economy_state,
                    {"last_lottery_draw_id": "new"},
                )
            ],
        )

    def test_bank_json_round_trips_reserved_economy_metadata(self):
        baba_class = load_baba_class()
        namespace = baba_class.refresh_bank_file.__globals__
        with TemporaryDirectory() as temp_dir:
            namespace["BANK_FILE"] = str(Path(temp_dir) / "bank.json")
            source = object.__new__(baba_class)
            source.bank_lock = threading.RLock()
            source.bank = {7: (123, True)}
            source.economy_state = {
                "last_lottery_draw_id": "2026-08-12",
                "pending_lottery_draw_id": "2026-08-13",
                "pending_lottery_numbers": [1, 2, 3, 4, 5, 6],
                "lottery_cleanup_draw_id": "2026-08-13",
                "casino_escrows": {
                    "poker:99:hand-1": {
                        "game_type": "poker",
                        "state": "open",
                        "contributions": {"7": 30},
                    }
                },
            }

            source.refresh_bank_file()

            payload = json.loads(
                Path(namespace["BANK_FILE"]).read_text(encoding="utf-8")
            )
            self.assertEqual(payload["7"], [123, True])
            self.assertEqual(
                payload["__bababucks_meta__"],
                source.economy_state,
            )

            loaded = object.__new__(baba_class)
            loaded.bank = {}
            loaded.economy_state = {
                "last_lottery_draw_id": "",
                "pending_lottery_draw_id": "",
                "pending_lottery_numbers": [],
                "lottery_cleanup_draw_id": "",
                "casino_escrows": {},
            }
            loaded.load_bank()

            self.assertEqual(loaded.bank, {7: (123, True)})
            self.assertEqual(
                loaded.economy_state,
                source.economy_state,
            )


if __name__ == "__main__":
    unittest.main()
