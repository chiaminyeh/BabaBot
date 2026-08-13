import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from bababucks import BababucksEscrow, BababucksLedger, InsufficientFunds


class BababucksLedgerTests(unittest.TestCase):
    def test_multi_account_transaction_commits_once_and_preserves_flags(self):
        bank = {1: (100, True), 2: (25, False)}
        writes = []
        ledger = BababucksLedger(
            bank,
            threading.RLock(),
            lambda: writes.append(dict(bank)),
        )

        balances = ledger.apply({1: -40, 2: 40})

        self.assertEqual(bank, {1: (60, True), 2: (65, False)})
        self.assertEqual(balances, {1: 60, 2: 65})
        self.assertEqual(writes, [{1: (60, True), 2: (65, False)}])

    def test_insufficient_funds_changes_nothing(self):
        bank = {1: (30, True), 2: (10, False)}
        writes = []
        ledger = BababucksLedger(
            bank,
            threading.RLock(),
            lambda: writes.append(dict(bank)),
        )

        with self.assertRaises(InsufficientFunds):
            ledger.apply({1: -31, 2: 31})

        self.assertEqual(bank, {1: (30, True), 2: (10, False)})
        self.assertEqual(writes, [])

    def test_persistence_failure_rolls_back_every_balance(self):
        bank = {1: (100, True), 2: (25, False)}

        def fail_write():
            raise OSError("disk full")

        ledger = BababucksLedger(bank, threading.RLock(), fail_write)

        with self.assertRaisesRegex(OSError, "disk full"):
            ledger.apply({1: -40, 2: 40})

        self.assertEqual(bank, {1: (100, True), 2: (25, False)})

    def test_concurrent_debits_cannot_overspend(self):
        bank = {1: (100, False)}
        writes = []
        ledger = BababucksLedger(
            bank,
            threading.RLock(),
            lambda: writes.append(bank[1][0]),
        )

        with ThreadPoolExecutor(max_workers=20) as pool:
            outcomes = list(pool.map(lambda _index: ledger.try_debit(1, 10), range(20)))

        self.assertEqual(sum(outcomes), 10)
        self.assertEqual(bank[1], (0, False))
        self.assertEqual(len(writes), 10)

    def test_invalid_amounts_are_rejected_without_writing(self):
        bank = {1: (100, False)}
        writes = []
        ledger = BababucksLedger(bank, threading.RLock(), lambda: writes.append(1))

        for deltas in ({1: 0}, {1: 1.5}, {True: -1}):
            with self.subTest(deltas=deltas):
                with self.assertRaises((TypeError, ValueError)):
                    ledger.apply(deltas)

        with self.assertRaises(ValueError):
            ledger.try_debit(1, 0)

        self.assertEqual(bank, {1: (100, False)})
        self.assertEqual(writes, [])

    def test_legacy_adjust_clamps_at_zero_and_preserves_flag(self):
        bank = {1: (5, True)}
        writes = []
        ledger = BababucksLedger(
            bank,
            threading.RLock(),
            lambda: writes.append(dict(bank)),
        )

        balance = ledger.adjust_clamped(1, -10)

        self.assertEqual(balance, 0)
        self.assertEqual(bank, {1: (0, True)})
        self.assertEqual(writes, [{1: (0, True)}])
        self.assertEqual(ledger.adjust_clamped(1, -1), 0)
        self.assertEqual(len(writes), 1)

    def test_apply_with_state_commits_balances_and_metadata_once(self):
        bank = {1: (100, True)}
        state = {"epoch": 4}
        writes = []
        ledger = BababucksLedger(
            bank,
            threading.RLock(),
            lambda: writes.append((dict(bank), dict(state))),
        )

        balances = ledger.apply_with_state(
            {1: 25}, state, {"epoch": 5, "last_settled_epoch": 4}
        )

        self.assertEqual(balances, {1: 125})
        self.assertEqual(bank, {1: (125, True)})
        self.assertEqual(state, {"epoch": 5, "last_settled_epoch": 4})
        self.assertEqual(
            writes,
            [({1: (125, True)}, {"epoch": 5, "last_settled_epoch": 4})],
        )

    def test_apply_with_state_rolls_back_balances_and_metadata_on_failure(self):
        bank = {1: (100, True)}
        state = {"epoch": 4}

        def fail_persist():
            raise OSError("disk full")

        ledger = BababucksLedger(bank, threading.RLock(), fail_persist)

        with self.assertRaisesRegex(OSError, "disk full"):
            ledger.apply_with_state({1: 25}, state, {"epoch": 5})

        self.assertEqual(bank, {1: (100, True)})
        self.assertEqual(state, {"epoch": 4})

    def test_apply_with_state_can_commit_metadata_without_balance_deltas(self):
        bank = {1: (100, True)}
        state = {"epoch": 4}
        writes = []
        ledger = BababucksLedger(
            bank,
            threading.RLock(),
            lambda: writes.append((dict(bank), dict(state))),
        )

        balances = ledger.apply_with_state({}, state, {"epoch": 5})

        self.assertEqual(balances, {})
        self.assertEqual(bank, {1: (100, True)})
        self.assertEqual(state, {"epoch": 5})
        self.assertEqual(writes, [({1: (100, True)}, {"epoch": 5})])


class BababucksEscrowTests(unittest.TestCase):
    def test_debit_tracks_each_players_cumulative_contribution(self):
        bank = {1: (100, True), 2: (80, False)}
        writes = []
        ledger = BababucksLedger(
            bank,
            threading.RLock(),
            lambda: writes.append(dict(bank)),
        )
        escrow = BababucksEscrow(ledger)

        self.assertTrue(escrow.try_debit(1, 10))
        self.assertTrue(escrow.try_debit(1, 25))
        self.assertTrue(escrow.try_debit(2, 20))

        self.assertEqual(escrow.contributions, {1: 35, 2: 20})
        self.assertEqual(escrow.total, 55)
        self.assertEqual(bank, {1: (65, True), 2: (60, False)})
        self.assertEqual(len(writes), 3)

    def test_group_debit_is_all_or_nothing(self):
        bank = {1: (100, True), 2: (5, False)}
        writes = []
        ledger = BababucksLedger(
            bank,
            threading.RLock(),
            lambda: writes.append(dict(bank)),
        )
        escrow = BababucksEscrow(ledger)

        self.assertFalse(escrow.try_debit_many({1: 10, 2: 10}))
        self.assertEqual(bank, {1: (100, True), 2: (5, False)})
        self.assertEqual(escrow.contributions, {})
        self.assertEqual(writes, [])

        bank[2] = (15, False)
        self.assertTrue(escrow.try_debit_many({1: 10, 2: 10}))
        self.assertEqual(bank, {1: (90, True), 2: (5, False)})
        self.assertEqual(escrow.contributions, {1: 10, 2: 10})
        self.assertEqual(len(writes), 1)

    def test_refund_returns_all_contributions_exactly_once(self):
        bank = {1: (100, True), 2: (80, False)}
        writes = []
        ledger = BababucksLedger(
            bank,
            threading.RLock(),
            lambda: writes.append(dict(bank)),
        )
        escrow = BababucksEscrow(ledger)
        escrow.try_debit(1, 10)
        escrow.try_debit(1, 25)
        escrow.try_debit(2, 20)

        self.assertTrue(escrow.refund())
        self.assertFalse(escrow.refund())

        self.assertEqual(bank, {1: (100, True), 2: (80, False)})
        self.assertEqual(escrow.state, "refunded")
        self.assertEqual(len(writes), 4)

    def test_settlement_credits_payouts_exactly_once(self):
        bank = {1: (100, True), 2: (80, False)}
        writes = []
        ledger = BababucksLedger(
            bank,
            threading.RLock(),
            lambda: writes.append(dict(bank)),
        )
        escrow = BababucksEscrow(ledger)
        escrow.try_debit(1, 35)
        escrow.try_debit(2, 20)

        self.assertTrue(escrow.settle({2: 70}))
        self.assertFalse(escrow.settle({2: 70}))
        self.assertFalse(escrow.refund())

        self.assertEqual(bank, {1: (65, True), 2: (130, False)})
        self.assertEqual(escrow.state, "settled")
        self.assertEqual(len(writes), 3)

    def test_reconstructed_durable_escrow_refunds_once(self):
        bank = {1: (100, True)}
        state = {"casino_escrows": {}}
        writes = []
        ledger = BababucksLedger(
            bank,
            threading.RLock(),
            lambda: writes.append((dict(bank), dict(state))),
        )
        first = BababucksEscrow(
            ledger,
            state=state,
            escrow_id="poker:99:hand-1",
            game_type="poker",
        )

        self.assertTrue(first.try_debit(1, 30))
        self.assertEqual(bank[1], (70, True))

        reconstructed = BababucksEscrow(
            ledger,
            state=state,
            escrow_id="poker:99:hand-1",
            game_type="poker",
        )
        self.assertEqual(reconstructed.contributions, {1: 30})
        self.assertTrue(reconstructed.refund())

        replay = BababucksEscrow(
            ledger,
            state=state,
            escrow_id="poker:99:hand-1",
            game_type="poker",
        )
        self.assertFalse(replay.refund())
        self.assertEqual(bank[1], (100, True))
        self.assertEqual(
            state["casino_escrows"]["poker:99:hand-1"]["state"],
            "refunded",
        )
        self.assertEqual(len(writes), 2)


if __name__ == "__main__":
    unittest.main()
