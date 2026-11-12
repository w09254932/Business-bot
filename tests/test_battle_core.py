import unittest

from battle_core import TIERS_INDIVIDUAL, TIERS_TEAM, compute_battle, points_for


class BattleRulesTests(unittest.TestCase):
    def test_every_individual_tier_start_and_end(self):
        for low, high, points in TIERS_INDIVIDUAL:
            with self.subTest(low=low):
                self.assertEqual(points_for(low, "individual"), points)
            if high != float("inf"):
                with self.subTest(high=high):
                    self.assertEqual(points_for(high, "individual"), points)

    def test_every_team_tier_start_and_end(self):
        for low, high, points in TIERS_TEAM:
            with self.subTest(low=low):
                self.assertEqual(points_for(low, "team"), points)
            if high != float("inf"):
                with self.subTest(high=high):
                    self.assertEqual(points_for(high, "team"), points)

    def test_home_uses_individual_tiers(self):
        self.assertEqual(points_for(25_000, "home"), points_for(25_000, "individual"))

    def test_win_formula(self):
        result = compute_battle(25_000, 18_000, "individual")
        self.assertEqual(result["my_points"], 20)
        self.assertEqual(result["opp_points"], 20)
        self.assertEqual(result["my_result"], 30)
        self.assertEqual(result["opp_result"], 10)
        self.assertEqual(result["result_label"], "فوز ✅")

    def test_loss_and_tie_behavior(self):
        loss = compute_battle(18_000, 25_000, "individual")
        tie = compute_battle(25_000, 25_000, "individual")
        self.assertEqual(loss["my_result"], 10)
        self.assertEqual(loss["opp_result"], 30)
        self.assertEqual(tie["result_label"], "خسارة ❌")

    def test_team_formula_uses_team_table(self):
        result = compute_battle(60_000, 50_000, "team")
        self.assertEqual(result["my_points"], 20)
        self.assertEqual(result["opp_points"], 20)
        self.assertEqual(result["my_result"], 30)

    def test_rejects_invalid_values(self):
        for args in [(-1, 3, "individual"), (True, 3, "individual"), (1.5, 3, "individual"), (1, 3, "unknown")]:
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    compute_battle(*args)


if __name__ == "__main__":
    unittest.main()
