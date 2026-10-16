import unittest
from unittest.mock import patch

from comment_share import CommentShareError, _provided_cover, forward_comment_share


class CommentShareCoverTests(unittest.TestCase):
    def setUp(self):
        self.cover = {
            "uri": "image-cut-tos-priv/example",
            "url_list": ["https://p3-pc-sign.douyinpic.com/image-cut-tos-priv/example~cover.jpeg?x-signature=fixture"],
        }
        self.cover_data = {"video_id": "123", "cover": self.cover}
        self.comment = {"cid": "456", "aweme_id": "123", "text": "fixture", "user": {"nickname": "author"}}

    def test_wrong_video_is_rejected_before_network_lookup(self):
        with patch("comment_share._resolve_recipient") as recipient:
            with self.assertRaises(CommentShareError):
                forward_comment_share(None, "789", "recipient", comment_id="456", cover_data=self.cover_data)
            recipient.assert_not_called()

    def test_missing_urls_are_rejected(self):
        with self.assertRaises(CommentShareError):
            _provided_cover({"video_id": "123", "cover": {"uri": "example", "url_list": []}}, "123")

    def test_non_douyin_or_insecure_urls_are_rejected(self):
        for url in ("https://douyinpic.com.example.com/x", "http://p3.douyinpic.com/x", "https://user:pass@p3.douyinpic.com/x"):
            with self.subTest(url=url), self.assertRaises(CommentShareError):
                _provided_cover({"video_id": "123", "cover": {"uri": "example", "url_list": [url]}}, "123")

    def test_provided_cover_preview_skips_detail_and_never_sends(self):
        with patch("comment_share._resolve_recipient", return_value={"nickname": "recipient", "uid": "321"}), \
                patch("comment_share._find_comment", return_value=self.comment), \
                patch("comment_share._find_cover") as detail, \
                patch("comment_share.DouyinAPI.create_conversation") as conversation, \
                patch("comment_share.DouyinAPI.send_comment_share") as send:
            result = forward_comment_share(None, "123", "recipient", comment_id="456", cover_data=self.cover_data)
            self.assertEqual(result["cover_source"], "provided")
            self.assertFalse(result["send_attempted"])
            detail.assert_not_called()
            conversation.assert_not_called()
            send.assert_not_called()

    def test_default_preview_still_fetches_video_cover(self):
        with patch("comment_share._resolve_recipient", return_value={"nickname": "recipient", "uid": "321"}), \
                patch("comment_share._find_comment", return_value=self.comment), \
                patch("comment_share._find_cover", return_value=self.cover) as detail:
            result = forward_comment_share(None, "123", "recipient", comment_id="456")
            detail.assert_called_once()
            self.assertEqual(result["cover_source"], "video_detail")

    def test_send_passes_supplied_cover_once(self):
        with patch("comment_share._validate_send_auth"), \
                patch("comment_share._resolve_recipient", return_value={"nickname": "recipient", "uid": "321"}), \
                patch("comment_share._find_comment", return_value=self.comment), \
                patch("comment_share._find_cover") as detail, \
                patch("comment_share.DouyinAPI.create_conversation", return_value=("conversation", 987, "fixture-ticket")), \
                patch("comment_share.DouyinAPI.send_comment_share", return_value={"ok": True, "status": 0}) as send:
            result = forward_comment_share(None, "123", "recipient", comment_id="456", cover_data=self.cover_data, send=True)
            self.assertTrue(result["sent"])
            self.assertEqual(send.call_count, 1)
            self.assertEqual(send.call_args.args[5], self.cover)
            detail.assert_not_called()


if __name__ == "__main__":
    unittest.main()
