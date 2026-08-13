import asyncio
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bababucks import BababucksEscrow, BababucksLedger
from lottery_cog import LotteryCog


class FakeBaba:
    def __init__(self, bank):
        self.bank = bank
        self.money_name = "bababucks"
        self.economy_state = {
            "last_lottery_draw_id": "",
            "pending_lottery_draw_id": "",
            "pending_lottery_numbers": [],
            "lottery_cleanup_draw_id": "",
        }
        self.writes = []
        self.fail_on_write = None

        def persist():
            write_number = len(self.writes) + 1
            self.writes.append(
                (dict(self.bank), dict(self.economy_state))
            )
            if self.fail_on_write == write_number:
                raise OSError("bank disk full")

        self.ledger = BababucksLedger(
            self.bank,
            threading.RLock(),
            persist,
        )

    def get_money(self, user_id):
        return self.ledger.get_balance(int(user_id))

    def try_debit_money(self, user_id, amount):
        return self.ledger.try_debit(int(user_id), int(amount))

    def credit_money(self, user_id, amount):
        return self.ledger.credit(int(user_id), int(amount))

    def apply_money_deltas(self, deltas):
        return self.ledger.apply({int(uid): int(delta) for uid, delta in deltas.items()})

    def apply_money_deltas_with_state(self, deltas, state_updates):
        return self.ledger.apply_with_state(
            {int(uid): int(delta) for uid, delta in deltas.items()},
            self.economy_state,
            state_updates,
        )

    def new_escrow(self):
        return BababucksEscrow(self.ledger)

    def refresh_bank_file(self):
        self.writes.append((dict(self.bank), dict(self.economy_state)))


class LotteryTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.baba = FakeBaba({7: (100, True)})
        self.cog = object.__new__(LotteryCog)
        self.cog.bot = SimpleNamespace(baba=self.baba)
        self.cog.ticket_cost = 100
        self.cog.prize_pool = 10000
        self.cog.lottery_file = str(Path(self.temp_dir.name) / "tickets.txt")
        self.cog._ticket_lock = threading.RLock()
        self.cog.announce_channel_id = []

    def test_manual_ticket_write_failure_refunds_debit_and_reports_no_success(self):
        def fail_write(_user_id, _numbers):
            raise OSError("ticket disk full")

        self.cog.save_ticket = fail_write
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=7),
            send=AsyncMock(),
        )

        asyncio.run(
            LotteryCog.buy_ticket.callback(self.cog, ctx, 1, 2, 3, 4, 5, 6)
        )

        self.assertEqual(self.baba.bank, {7: (100, True)})
        sent_messages = [call.args[0] for call in ctx.send.await_args_list]
        self.assertEqual(len(sent_messages), 1)
        self.assertIn("failed", sent_messages[0].lower())
        self.assertNotIn("purchased", sent_messages[0].lower())

    def test_random_ticket_batch_write_failure_refunds_full_cost(self):
        def fail_batch(_user_id, _tickets):
            raise OSError("ticket disk full")

        self.baba.bank[7] = (300, True)
        self.cog.save_tickets = fail_batch
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=7),
            send=AsyncMock(),
        )

        asyncio.run(
            LotteryCog.buy_random_ticket.callback(self.cog, ctx, count=3)
        )

        self.assertEqual(self.baba.bank, {7: (300, True)})
        self.assertEqual(self.cog.get_tickets(), [])
        sent_messages = [call.args[0] for call in ctx.send.await_args_list]
        self.assertEqual(len(sent_messages), 1)
        self.assertIn("failed", sent_messages[0].lower())
        self.assertNotIn("successfully purchased", sent_messages[0].lower())

    def test_pending_draw_pauses_manual_and_random_ticket_sales(self):
        self.baba.bank[7] = (300, True)
        self.baba.economy_state["pending_lottery_draw_id"] = "2026-08-13"
        manual_ctx = SimpleNamespace(
            author=SimpleNamespace(id=7), send=AsyncMock()
        )
        random_ctx = SimpleNamespace(
            author=SimpleNamespace(id=7), send=AsyncMock()
        )

        asyncio.run(
            LotteryCog.buy_ticket.callback(
                self.cog, manual_ctx, 1, 2, 3, 4, 5, 6
            )
        )
        asyncio.run(
            LotteryCog.buy_random_ticket.callback(
                self.cog, random_ctx, count=2
            )
        )

        self.assertEqual(self.baba.bank, {7: (300, True)})
        self.assertEqual(self.cog.get_tickets(), [])
        self.assertIn(
            "temporarily paused",
            manual_ctx.send.await_args.args[0].lower(),
        )
        self.assertIn(
            "temporarily paused",
            random_ctx.send.await_args.args[0].lower(),
        )

    def test_draw_aggregates_duplicate_winner_deltas_and_retries_once(self):
        self.baba.bank[7] = (100, True)
        self.cog.save_tickets(
            7,
            [
                [1, 2, 3, 10, 11, 12],
                [4, 5, 6, 13, 14, 15],
            ],
        )

        asyncio.run(
            self.cog.run_draw("2026-08-13", [1, 2, 3, 4, 5, 6])
        )
        writes_after_first_draw = len(self.baba.writes)
        asyncio.run(
            self.cog.run_draw("2026-08-13", [1, 2, 3, 4, 5, 6])
        )

        self.assertEqual(self.baba.bank[7], (500, True))
        self.assertEqual(self.cog.get_tickets(), [])
        self.assertEqual(
            self.baba.economy_state["last_lottery_draw_id"],
            "2026-08-13",
        )
        self.assertEqual(len(self.baba.writes), writes_after_first_draw)

    def test_draw_persist_failure_keeps_tickets_and_does_not_announce(self):
        self.baba.bank[7] = (100, True)
        self.cog.save_ticket(7, [1, 2, 3, 10, 11, 12])
        channel = SimpleNamespace(send=AsyncMock())
        self.cog.announce_channel_id = [99]
        self.cog.bot.get_channel = lambda channel_id: channel
        self.baba.fail_on_write = 2

        with self.assertRaisesRegex(OSError, "bank disk full"):
            asyncio.run(
                self.cog.run_draw(
                    "2026-08-13", [1, 2, 3, 4, 5, 6]
                )
            )

        self.assertEqual(self.baba.bank[7], (100, True))
        self.assertEqual(len(self.cog.get_tickets()), 1)
        self.assertEqual(
            self.baba.economy_state["last_lottery_draw_id"], ""
        )
        channel.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
