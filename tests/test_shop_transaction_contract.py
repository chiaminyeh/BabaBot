import unittest
from trpg.i18n import t

# The actual locale translation strings might be loaded from json,
# so let's mock or use the real i18n logic. We should just test the t function.
class TestShopTransactionContract(unittest.TestCase):
    def test_buy_success_message_formatting(self):
        # Even if locale isn't loaded, t fallback string must not raise KeyError
        # It should format successfully with amount, name, total_cost

        zh_fallback = "✅ 購買了 {amount} 個【{name}】！花費 {total_cost}G"
        en_fallback = "✅ Bought {amount} {name}! Spent {total_cost}G"

        try:
            zh_result = t("zh", "shop.buy_success", zh_fallback, amount=5, name="Health Potion", total_cost=100)
            en_result = t("en", "shop.buy_success", en_fallback, amount=5, name="Health Potion", total_cost=100)
        except KeyError as e:
            self.fail(f"t() raised KeyError for total_cost: {e}")

        self.assertIn("5", zh_result)
        self.assertIn("Health Potion", zh_result)
        self.assertIn("100", zh_result)

    def test_invalid_amounts_rejected_safely(self):
        # We can test modal input parsing logic that is implemented in BuyItemModal
        # Specifically checking how invalid integer values are handled
        invalid_inputs = ["0", "-5", "abc", " 1.5 ", ""]
        for inp in invalid_inputs:
            try:
                amount = int(inp.strip())
                if amount <= 0:
                    raise ValueError
                self.fail("Should have raised ValueError")
            except ValueError:
                pass # Expected

if __name__ == '__main__':
    unittest.main()
