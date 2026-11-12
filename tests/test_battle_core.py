import unittest
from battle_core import TIERS_INDIVIDUAL, TIERS_TEAM, compute_battle, points_for


class BattleRulesTests(unittest.TestCase):
    def test_every_individual_tier_start(self):
        for low, high, points in TIERS_INDIVIDUAL:
            with self.subTest(low=low):
                self.assertEqual(points_for(low, "individual"), points)

    def test_every_team_tier_start(self):
        for low, high, points in TIERS_TEAM:
            with self.subTest(low=low):
                self.assertEqual(points_for(low, "team"), points)

    def test_home_uses_individual_tiers(self):
        self.assertEqual(points_for(25000, "home"), points_for(25000, "individual"))

    def test_win_formula(self):
        result = compute_battle(25000, 18000, "individual")
        self.assertEqual(result["my_points"], 20)
        self.assertEqual(result["opp_points"], 20)
        self.assertEqual(result["my_result"], 30)
        self.assertEqual(result["opp_result"], 10)
        self.assertEqual(result["result_label"], "فوز ✅")

    def test_loss_and_tie_behavior(self):
        loss = compute_battle(18000, 25000, "individual")
        tie = compute_battle(25000, 25000, "individual")
        self.assertEqual(loss["my_result"], 10)
        self.assertEqual(loss["opp_result"], 30)
        self.assertEqual(tie["result_label"], "خسارة ❌")


if __name__ == "__main__":
    unittest.main()
