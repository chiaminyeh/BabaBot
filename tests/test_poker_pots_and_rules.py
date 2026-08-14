import unittest
from types import SimpleNamespace

from poker.models import Player, TableConfig
from poker.pots import PokerChipEscrow, PotManager
from poker.rules import HandEvaluator, TableRules


class PokerPotsAndRulesTests(unittest.TestCase):
    @staticmethod
    def make_player(uid: int, name: str, stack: int, seat_index: int) -> Player:
        p = Player(SimpleNamespace(id=uid, name=name))
        p.stack = stack
        p.seat_index = seat_index
        return p

    def test_uncalled_bet_is_calculated_and_returned_to_bettor(self):
        p1 = self.make_player(1, "Alice", 100, 0)
        p2 = self.make_player(2, "Bob", 40, 1)
        p3 = self.make_player(3, "Charlie", 10, 2)
        escrow = PokerChipEscrow([p1, p2, p3])

        # Debits
        self.assertTrue(escrow.try_debit(1, 100))
        self.assertTrue(escrow.try_debit(2, 40))
        self.assertTrue(escrow.try_debit(3, 10))
        p3.folded = True

        uncalled_id, uncalled_amt = PotManager.calculate_uncalled_bet(
            [p1, p2, p3], escrow.contributions
        )
        self.assertEqual(uncalled_id, 1)
        self.assertEqual(uncalled_amt, 60)  # 100 - 40

        self.assertTrue(escrow.return_uncalled_bet(uncalled_id, uncalled_amt))
        self.assertEqual(escrow.contributions[1], 40)
        self.assertEqual(p1.stack, 60)
        self.assertEqual(escrow.total, 90)

    def test_multi_level_side_pots_isolate_eligible_contenders(self):
        # 4 players with different stacks:
        # P1: 20 (all-in)
        # P2: 50 (all-in)
        # P3: 100 (all-in)
        # P4: 100
        p1 = self.make_player(1, "P1", 0, 0)
        p2 = self.make_player(2, "P2", 0, 1)
        p3 = self.make_player(3, "P3", 0, 2)
        p4 = self.make_player(4, "P4", 50, 3)
        p1.all_in = True
        p2.all_in = True
        p3.all_in = True

        contributions = {1: 20, 2: 50, 3: 100, 4: 100}
        side_pots = PotManager.build_side_pots([p1, p2, p3, p4], contributions)

        self.assertEqual(len(side_pots), 3)
        # Main pot (Level 20): 20 * 4 = 80 chips. Eligible: P1, P2, P3, P4
        self.assertEqual(side_pots[0].amount, 80)
        self.assertEqual(side_pots[0].eligible_player_ids, [1, 2, 3, 4])

        # Side pot 1 (Level 50): (50 - 20) * 3 = 90 chips. Eligible: P2, P3, P4
        self.assertEqual(side_pots[1].amount, 90)
        self.assertEqual(side_pots[1].eligible_player_ids, [2, 3, 4])

        # Side pot 2 (Level 100): (100 - 50) * 2 = 100 chips. Eligible: P3, P4
        self.assertEqual(side_pots[2].amount, 100)
        self.assertEqual(side_pots[2].eligible_player_ids, [3, 4])

        self.assertEqual(sum(sp.amount for sp in side_pots), 270)

    def test_odd_chip_allocation_clockwise_from_button(self):
        # 3-way tie on a 10 chip pot (3 chips each, 1 remainder)
        # Button is seat 0. Seats: P1 (seat 0), P2 (seat 1), P3 (seat 2)
        # Clockwise from button: first seat is Seat 1 (P2), then Seat 2 (P3), then Seat 0 (P1)
        p1 = self.make_player(1, "Seat0", 0, 0)
        p2 = self.make_player(2, "Seat1", 0, 1)
        p3 = self.make_player(3, "Seat2", 0, 2)
        for p in (p1, p2, p3):
            p.hand = ["A♠", "K♦"]

        contributions = {1: 10, 2: 10, 3: 10}
        community = ["2♣", "3♣", "4♣", "8♦", "9♦"]

        # Button is at seat 0
        payouts, side_pots = PotManager.resolve_showdown_payouts(
            players=[p1, p2, p3],
            contributions=contributions,
            rank_fn=lambda cards: (0, [14, 13]),  # All equal
            compare_fn=lambda s1, s2: 0,
            community=community,
            button_seat=0,
        )

        self.assertEqual(sum(payouts.values()), 30)
        # 30 // 3 = 10 chips each, no remainder
        self.assertEqual(payouts, {1: 10, 2: 10, 3: 10})

        # Now test with a pot having 1 odd chip (e.g. 16 chips between 3 players -> 5 each + 1 remainder to Seat 1)
        odd_contribs = {1: 6, 2: 5, 3: 5}  # total 16
        payouts_odd, _ = PotManager.resolve_showdown_payouts(
            players=[p1, p2, p3],
            contributions=odd_contribs,
            rank_fn=lambda cards: (0, [14, 13]),
            compare_fn=lambda s1, s2: 0,
            community=community,
            button_seat=0,
        )
        # Level 5 pot with 16 total chips (no all-in) -> 5 chips each + 1 remainder to Seat 1 (P2).
        self.assertEqual(payouts_odd, {2: 6, 3: 5, 1: 5})

    def test_wheel_straight_ranks_as_five_high(self):
        wheel = HandEvaluator.rank_hand(["A♠", "2♦", "3♣", "4♥", "5♠", "K♦", "Q♣"])
        six_high = HandEvaluator.rank_hand(["2♠", "3♦", "4♣", "5♥", "6♠", "K♦", "Q♣"])
        broadway = HandEvaluator.rank_hand(["10♠", "J♦", "Q♣", "K♥", "A♠", "2♦", "3♣"])

        self.assertEqual(wheel, (4, [5]))
        self.assertEqual(six_high, (4, [6]))
        self.assertEqual(broadway, (4, [14]))

        self.assertEqual(HandEvaluator.compare(wheel, six_high), -1)
        self.assertEqual(HandEvaluator.compare(six_high, broadway), -1)
        self.assertEqual(HandEvaluator.compare(wheel, wheel), 0)

    def test_royal_flush_beats_straight_flush(self):
        rf = HandEvaluator.rank_hand(["A♠", "K♠", "Q♠", "J♠", "10♠", "2♦", "3♣"])
        sf_nine = HandEvaluator.rank_hand(["9♥", "8♥", "7♥", "6♥", "5♥", "2♦", "3♣"])

        self.assertEqual(rf, (8, [14]))
        self.assertEqual(sf_nine, (8, [9]))
        self.assertEqual(HandEvaluator.compare(rf, sf_nine), 1)

    def test_full_house_and_flush_comparison(self):
        fh_aces = HandEvaluator.rank_hand(["A♠", "A♥", "A♦", "K♣", "K♦", "2♣", "3♦"])
        fh_kings = HandEvaluator.rank_hand(["K♠", "K♥", "K♦", "A♣", "A♦", "2♣", "3♦"])
        flush = HandEvaluator.rank_hand(["A♠", "J♠", "8♠", "6♠", "2♠", "K♦", "Q♣"])

        self.assertEqual(fh_aces, (6, [14, 13]))
        self.assertEqual(fh_kings, (6, [13, 14]))
        self.assertEqual(HandEvaluator.compare(fh_aces, fh_kings), 1)
        self.assertEqual(HandEvaluator.compare(fh_kings, flush), 1)

    def test_legal_action_summary_and_presets(self):
        p = self.make_player(1, "Hero", 200, 0)
        p.bet = 10

        legal = TableRules.calculate_legal_actions(
            player=p,
            highest_bet=30,
            big_blind=10,
            last_raise_size=20,
            acted_players=set(),
            pot=100,
        )

        self.assertTrue(legal.can_fold)
        self.assertFalse(legal.can_check)
        self.assertTrue(legal.can_call)
        self.assertEqual(legal.call_amount, 20)
        self.assertTrue(legal.can_raise)
        self.assertEqual(legal.min_raise_to, 50)  # 30 + 20
        self.assertEqual(legal.max_raise_to, 210)  # 10 + 200

        self.assertTrue(len(legal.presets) >= 3)
        self.assertEqual(legal.presets[0].label, "Min")
        self.assertEqual(legal.presets[0].target_total, 50)


if __name__ == "__main__":
    unittest.main()
