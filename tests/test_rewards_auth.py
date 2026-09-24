import json
import tempfile
import unittest
from pathlib import Path

import rewards_earn


class RewardsAuthTests(unittest.TestCase):
    def test_mail_error_is_actionable_and_categorized(self):
        body = rewards_earn._build_mail_body(
            True, "TOU_REQUIRED: Microsoft 要求重新确认账户条款"
        )
        self.assertIn("任务执行失败 [TOU_REQUIRED]", body)
        self.assertIn("含义：Microsoft 要求重新确认账户条款或重新认证", body)
        self.assertIn("自动动作：已写入 auth_required.json", body)
        self.assertIn("执行 `python rewards_earn.py export`", body)

    def test_mail_unknown_error_explains_limits_and_next_step(self):
        body = rewards_earn._build_mail_body(True, "SOME_NEW_ERROR: test")
        self.assertIn("含义：脚本遇到未分类异常", body)
        self.assertIn("已停止任务并保留现有登录态", body)

    def test_auth_wall_urls_are_detected(self):
        self.assertTrue(
            rewards_earn._is_auth_wall_url(
                "https://account.live.com/tou/accrue?mkt=EN-US"
            )
        )
        self.assertTrue(rewards_earn._is_auth_wall_url("https://login.live.com/"))
        self.assertFalse(rewards_earn._is_auth_wall_url("https://rewards.bing.com/earn"))
        self.assertEqual(
            rewards_earn._auth_error("https://account.live.com/tou/accrue").code,
            "TOU_REQUIRED",
        )

    def test_display_url_does_not_expose_query_parameters(self):
        self.assertEqual(
            rewards_earn._display_url(
                "https://account.live.com/tou/accrue?ru=https%3A%2F%2Frewards.bing.com"
            ),
            "https://account.live.com/tou/accrue",
        )

    def test_storage_state_is_structurally_validated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "storage_state.json"
            path.write_text(
                json.dumps({"cookies": [], "origins": []}), encoding="utf-8"
            )
            state = rewards_earn._read_storage_state(path)
            self.assertEqual(state["cookies"], [])
            self.assertEqual(state["origins"], [])

            path.write_text(json.dumps({"cookies": {}}), encoding="utf-8")
            with self.assertRaises(rewards_earn.StorageStateError):
                rewards_earn._read_storage_state(path)

    def test_auth_status_is_atomic_and_cleared_after_state_persist(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            old = rewards_earn.IS_NAS
            old_path = rewards_earn.NAS_STORAGE_STATE
            old_status_path = rewards_earn._auth_status_path
            try:
                rewards_earn.IS_NAS = True
                rewards_earn.NAS_STORAGE_STATE = str(Path(temp_dir) / "storage_state.json")
                rewards_earn._auth_status_path = lambda: str(Path(temp_dir) / "auth_required.json")
                error = rewards_earn._auth_error("https://account.live.com/tou/accrue")
                rewards_earn._mark_auth_required(error)
                marker = Path(temp_dir) / "auth_required.json"
                self.assertTrue(marker.exists())
                self.assertEqual(json.loads(marker.read_text(encoding="utf-8"))["code"], "TOU_REQUIRED")
                rewards_earn._clear_auth_required()
                self.assertFalse(marker.exists())
            finally:
                rewards_earn.IS_NAS = old
                rewards_earn.NAS_STORAGE_STATE = old_path
                rewards_earn._auth_status_path = old_status_path


class RewardsAuthAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_rescan_does_not_turn_auth_redirect_into_success(self):
        class AuthRedirectPage:
            url = "https://rewards.bing.com/earn"

            async def goto(self, *_args, **_kwargs):
                self.url = "https://account.live.com/tou/accrue"

            async def wait_for_timeout(self, *_args, **_kwargs):
                return None

        with self.assertRaises(rewards_earn.AuthenticationRequired) as caught:
            await rewards_earn._scan_unfinished(AuthRedirectPage())
        self.assertTrue(str(caught.exception).startswith("TOU_REQUIRED:"))


if __name__ == "__main__":
    unittest.main()
