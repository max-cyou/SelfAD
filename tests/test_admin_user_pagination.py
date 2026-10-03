import unittest

from selfad.routes.admin_shared import USERS_PAGE_SIZE, _pagination_items


class AdminUserPaginationTests(unittest.TestCase):
    def test_users_are_limited_to_ten_per_page(self):
        self.assertEqual(USERS_PAGE_SIZE, 10)

    def test_short_pagination_lists_every_page(self):
        self.assertEqual(_pagination_items(3, 5), [1, 2, 3, 4, 5])

    def test_long_pagination_keeps_edges_and_nearby_pages(self):
        self.assertEqual(
            _pagination_items(6, 12),
            [1, None, 5, 6, 7, None, 12],
        )


if __name__ == "__main__":
    unittest.main()
