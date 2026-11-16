import unittest
from tt_plus_search import plain_words, without_links, wants_links


class NaturalChatTests(unittest.TestCase):
    def test_screenshot_domains(self):
        for domain in ("esports.op.gg", "blast.tv", "来源：esports.op.gg"):
            for text in (f"我偏这边。 ({domain})", f"我偏这边。（{domain}）"):
                self.assertNotIn(domain, plain_words(text))
                self.assertNotIn(domain, without_links(text))

    def test_opinion_does_not_request_links(self):
        self.assertFalse(wants_links("你觉得ig和geng谁更强"))
        self.assertFalse(wants_links("你觉得cs的猎鹰和绿龙谁厉害"))

    def test_keep_requested_links_and_normal_parentheses(self):
        self.assertTrue(wants_links("给我这个视频的链接"))
        self.assertEqual(plain_words("链接 https://example.com/video"), "链接 https://example.com/video")
        self.assertEqual(plain_words("先看版本号（v2）"), "先看版本号（v2）")


if __name__ == "__main__":
    unittest.main()
