import unittest

from poker_ai_strategy import RLCardRuleStrategy


class RLCardRuleStrategyTests(unittest.TestCase):
    def setUp(self):
        self.strategy = RLCardRuleStrategy(big_blind=10)

    def test_premium_preflop_hand_raises(self):
        decision = self.strategy.decide(
            ["A♠", "K♦"],
            [],
            pot=15,
            current_bet=0,
            highest_bet=0,
            stack=200,
        )

        self.assertEqual(decision.action, "bet")
        self.assertGreaterEqual(decision.amount, 10)

    def test_raise_uses_previous_full_raise_size(self):
        decision = self.strategy.decide(
            ["A♠", "K♦"],
            [],
            pot=100,
            current_bet=10,
            highest_bet=30,
            last_raise_size=20,
            stack=100,
        )

        # The player must call 20 and add a full 20-chip raise, so the
        # smallest legal increment is 40 (total bet 50).
        self.assertEqual(decision.action, "bet")
        self.assertGreaterEqual(decision.amount, 40)

    def test_low_flop_with_no_pair_or_draw_checks_when_free(self):
        decision = self.strategy.decide(
            ["K♠", "Q♦"],
            ["2♣", "4♦", "5♥"],
            pot=15,
            current_bet=0,
            highest_bet=0,
            stack=200,
        )

        self.assertEqual(decision.action, "check")
        self.assertEqual(decision.amount, 0)

    def test_unacceptable_preflop_hand_folds_when_facing_bet(self):
        decision = self.strategy.decide(
            ["7♠", "2♦"],
            [],
            pot=25,
            current_bet=0,
            highest_bet=10,
            stack=200,
        )

        self.assertEqual(decision.action, "fold")
        self.assertEqual(decision.amount, 0)

    def test_unacceptable_preflop_hand_checks_when_no_bet_is_faced(self):
        decision = self.strategy.decide(
            ["7♠", "2♦"],
            [],
            pot=25,
            current_bet=10,
            highest_bet=10,
            stack=200,
        )

        self.assertEqual(decision.action, "check")
        self.assertEqual(decision.amount, 0)

    def test_raise_becomes_allin_when_stack_cannot_make_minimum_raise(self):
        decision = self.strategy.decide(
            ["A♠", "K♦"],
            [],
            pot=25,
            current_bet=0,
            highest_bet=10,
            stack=15,
        )

        self.assertEqual(decision.action, "allin")
        self.assertEqual(decision.amount, 0)

    def test_call_becomes_allin_when_call_uses_last_chip(self):
        decision = self.strategy.decide(
            ["K♠", "Q♦"],
            ["9♣", "8♦", "7♥"],
            pot=25,
            current_bet=0,
            highest_bet=10,
            stack=10,
        )

        self.assertEqual(decision.action, "allin")
        self.assertEqual(decision.amount, 0)

    def test_unmatched_pocket_pair_checks_on_a_low_flop_like_rlcard(self):
        decision = self.strategy.decide(
            ["7♠", "7♦"],
            ["2♣", "4♦", "5♥"],
            pot=15,
            current_bet=0,
            highest_bet=0,
            stack=200,
        )

        self.assertEqual(decision.action, "check")

    def test_suited_broadway_ace_uses_rlcard_flush_branch_on_a_matching_flop(self):
        decision = self.strategy.decide(
            ["A♠", "K♠"],
            ["2♠", "4♦", "5♥"],
            pot=15,
            current_bet=0,
            highest_bet=0,
            stack=200,
        )

        self.assertEqual(decision.action, "bet")


if __name__ == "__main__":
    unittest.main()
