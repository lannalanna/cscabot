import unittest

import testbot


class TestTestbotHelpers(unittest.TestCase):
    def test_normalize_lang(self):
        self.assertEqual(testbot._normalize_lang("ru"), "ru")
        self.assertEqual(testbot._normalize_lang("ru-RU"), "ru")
        self.assertEqual(testbot._normalize_lang("en"), "en")
        self.assertEqual(testbot._normalize_lang("zh"), "en")
        self.assertEqual(testbot._normalize_lang(""), "en")

    def test_txt_switch(self):
        self.assertEqual(testbot._txt("ru", "Привет", "Hello"), "Привет")
        self.assertEqual(testbot._txt("en", "Привет", "Hello"), "Hello")
        self.assertEqual(testbot._txt("de", "Привет", "Hello"), "Hello")

    def test_correct_phrase_language(self):
        ru_set = set(testbot.CORRECT_PHRASES_RU)
        en_set = set(testbot.CORRECT_PHRASES_EN)

        for _ in range(30):
            self.assertIn(testbot._correct_phrase("ru"), ru_set)
            self.assertIn(testbot._correct_phrase("en"), en_set)


if __name__ == "__main__":
    unittest.main()

