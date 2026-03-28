import asyncio
import importlib
import os
import tempfile
import unittest


class DummyUser:
    def __init__(self, uid: int, username: str, language_code: str):
        self.id = uid
        self.username = username
        self.language_code = language_code


class TestDbLanguageAndRecommendations(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_bot.db")
        os.environ["BOT_DB_PATH"] = self.db_path

        import db as db_module
        self.db = importlib.reload(db_module)

    def tearDown(self):
        self.tmpdir.cleanup()
        if "BOT_DB_PATH" in os.environ:
            del os.environ["BOT_DB_PATH"]

    def test_set_and_get_user_language(self):
        async def _run():
            conn = await self.db.init_db()
            user = DummyUser(1001, "alice", "ru")
            await self.db.ensure_user(conn, user, None)

            lang0 = await self.db.get_user_language(conn, user.id)
            self.assertEqual(lang0, "ru")

            await self.db.set_user_language(conn, user.id, "en")
            lang1 = await self.db.get_user_language(conn, user.id)
            self.assertEqual(lang1, "en")
            await conn.close()

        asyncio.run(_run())

    def test_save_exam_recommendations(self):
        async def _run():
            conn = await self.db.init_db()
            user = DummyUser(1002, "bob", "en")
            await self.db.ensure_user(conn, user, None)

            payload = '["inequalities","functions","geometry"]'
            await self.db.save_user_exam_recommendations(
                conn,
                user_id=user.id,
                exam_type="mar",
                topics_json=payload,
            )

            cur = await conn.execute(
                """
                SELECT user_id, exam_type, topics_json
                FROM user_exam_recommendations
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (user.id,),
            )
            row = await cur.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], user.id)
            self.assertEqual(row[1], "mar")
            self.assertEqual(row[2], payload)
            await conn.close()

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()

