import copy
import math
import unittest

import numpy as np

from signalclassifier.optimization import normalize_optimization, sample_configurations


class OptimizationPlanningTests(unittest.TestCase):
    def test_defaults_are_bounded_and_normalization_preserves_input(self):
        original = {"segment_length": 65536, "optimization": {"enabled": True}}
        before = copy.deepcopy(original)
        result = normalize_optimization(original)
        self.assertEqual(original, before)
        self.assertEqual(result["search_space"]["segment_lengths"], [32768, 65536])
        self.assertEqual(result["max_trials"], 12)
        self.assertEqual(result["timeout_seconds"], 300)
        self.assertEqual(result["objective"], "score")
        self.assertFalse(normalize_optimization({})["enabled"])
        self.assertEqual(normalize_optimization({"segment_length": 256})["search_space"]["segment_lengths"], [256, 512])

    def test_invalid_settings_are_rejected_before_planning(self):
        invalid = [
            {"enabled": "true"}, {"method": "bayesian"}, {"objective": "test_accuracy"},
            {"max_trials": True}, {"max_trials": 0}, {"max_trials": 51}, {"max_trials": 2.0},
            {"timeout_seconds": math.nan}, {"timeout_seconds": math.inf}, {"timeout_seconds": -1},
            {"timeout_seconds": 86401}, {"timeout_seconds": 10**1000}, {"unexpected": 1},
            {"search_space": []}, {"search_space": {"unknown": [1]}},
            {"search_space": {"segment_lengths": []}}, {"search_space": {"segment_lengths": [True]}},
            {"search_space": {"segment_lengths": [255]}}, {"search_space": {"segment_lengths": [65537]}},
            {"search_space": {"batch_sizes": [3]}}, {"search_space": {"epochs": [101]}},
            {"search_space": {"learning_rate_min": .02, "learning_rate_max": .001}},
            {"search_space": {"learning_rate_min": 0}}, {"search_space": {"learning_rate_max": .2}},
        ]
        for settings in invalid:
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                normalize_optimization({"optimization": settings})
        with self.assertRaisesRegex(ValueError, "Validierung"):
            normalize_optimization({"evaluation_mode": "training_only", "optimization": {"enabled": True}})
        self.assertFalse(normalize_optimization({"evaluation_mode": "training_only"})["enabled"])

    def test_neural_search_is_seeded_unique_and_log_uniform(self):
        settings = normalize_optimization({"optimization": {"enabled": True, "max_trials": 50,
            "search_space": {"segment_lengths": [1024, 4096], "learning_rate_min": 1e-5,
                             "learning_rate_max": .1, "batch_sizes": [8, 32], "epochs": [1, 5]}}})
        first = sample_configurations(settings, 42)
        self.assertEqual(first, sample_configurations(settings, 42))
        self.assertNotEqual(first, sample_configurations(settings, 43))
        self.assertEqual(len(first), 50)
        self.assertEqual(len({tuple(item.items()) for item in first}), 50)
        for proposal in first:
            self.assertEqual(set(proposal), {"segment_length", "learning_rate", "batch_size", "epochs"})
            self.assertIn(proposal["segment_length"], [1024, 4096])
            self.assertIn(proposal["batch_size"], [8, 32])
            self.assertIn(proposal["epochs"], [1, 5])
            self.assertTrue(1e-5 <= proposal["learning_rate"] <= .1)
        # A log-uniform draw distributes substantial mass below the geometric
        # midpoint; linear-uniform sampling would almost never do so here.
        low_half = sum(item["learning_rate"] < .001 for item in first)
        self.assertTrue(10 < low_half < 40)

    def test_finite_search_space_exhausts_without_duplicate_trials(self):
        settings = normalize_optimization({"optimization": {"enabled": True, "max_trials": 50,
            "search_space": {"segment_lengths": [1024, 2048], "learning_rate_min": .001,
                             "learning_rate_max": .001, "batch_sizes": [16], "epochs": [1, 2]}}})
        proposals = sample_configurations(settings, 9)
        self.assertEqual(len(proposals), 4)
        self.assertEqual({(item["segment_length"], item["epochs"]) for item in proposals}, {(1024, 1), (1024, 2), (2048, 1), (2048, 2)})
        self.assertEqual(proposals, sample_configurations(settings, 9))

    def test_feature_lda_only_varies_effective_segment_hyperparameter(self):
        settings = normalize_optimization({"optimization": {"enabled": True, "max_trials": 50,
            "search_space": {"segment_lengths": [256, 512, 1024], "batch_sizes": [16, 32], "epochs": [5, 10]}}})
        proposals = sample_configurations(settings, 7, "feature_lda")
        self.assertEqual(len(proposals), 3)
        self.assertEqual({item["segment_length"] for item in proposals}, {256, 512, 1024})
        self.assertEqual(len({(item["learning_rate"], item["batch_size"], item["epochs"]) for item in proposals}), 1)
        self.assertEqual(sample_configurations(normalize_optimization({}), 42), [])

    def test_seed_and_tiny_representable_rate_interval_are_bounded(self):
        settings = normalize_optimization({"optimization": {"enabled": True, "max_trials": 50,
            "search_space": {"segment_lengths": [1024], "batch_sizes": [16], "epochs": [1],
                             "learning_rate_min": .001, "learning_rate_max": float(np.nextafter(.001, math.inf))}}})
        proposals = sample_configurations(settings, 5)
        self.assertTrue(1 <= len(proposals) <= 2)
        for seed in (True, -1, 2**31, .5):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                sample_configurations(settings, seed)


if __name__ == "__main__":
    unittest.main()
