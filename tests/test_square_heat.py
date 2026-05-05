import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "auto_mirror.py"
SPEC = importlib.util.spec_from_file_location("auto_mirror", SCRIPT)
auto_mirror = importlib.util.module_from_spec(SPEC)
sys.modules["auto_mirror"] = auto_mirror
SPEC.loader.exec_module(auto_mirror)


class SquareHeatTest(unittest.TestCase):
    def test_square_heat_normalizes_filters_and_scores_token_posts(self):
        now = auto_mirror.utc_now()
        posts = [
            auto_mirror.normalize_square_post(
                {
                    "postId": "1",
                    "author": {"userId": "u1", "nickname": "alice"},
                    "bodyTextOnly": "BTC momentum is rising again $BTC",
                    "createTime": int(now.timestamp() * 1000),
                    "likeCount": 10,
                    "commentCount": 2,
                    "shareCount": 1,
                    "viewCount": 100,
                }
            ),
            auto_mirror.normalize_square_post(
                {
                    "postId": "2",
                    "author": {"userId": "u2", "nickname": "bob"},
                    "bodyTextOnly": "Only talking about ETH here",
                    "createTime": int(now.timestamp() * 1000),
                }
            ),
        ]

        token_posts = auto_mirror.filter_token_posts([post for post in posts if post], "BTC")
        report = auto_mirror.score_heat(token_posts, "BTC", window_hours=24)

        self.assertEqual(report.posts_in_window, 1)
        self.assertEqual(report.unique_authors, 1)
        self.assertEqual(report.weighted_engagement, 17)
        self.assertGreater(report.heat_score, 0)


if __name__ == "__main__":
    unittest.main()
