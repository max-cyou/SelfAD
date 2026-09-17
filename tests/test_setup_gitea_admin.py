import unittest
from unittest.mock import MagicMock, patch

from selfad.gitea import GiteaUser
from selfad.models import User
from selfad.routes.setup import submit_setup


class SetupGiteaAdminTests(unittest.TestCase):
    def test_setup_binds_panel_admin_to_gitea_root(self):
        request = MagicMock()
        session = MagicMock()
        session.get.return_value = None
        settings = MagicMock()

        with (
            patch("selfad.routes.setup.csrf_token_is_valid", return_value=True),
            patch("selfad.routes.setup.get_gitea_settings", return_value=settings),
            patch("selfad.routes.setup.update_gitea_user") as update_user,
            patch(
                "selfad.routes.setup.authenticate_gitea_user",
                return_value=GiteaUser(1, "root"),
            ) as authenticate,
            patch("selfad.routes.setup.set_gitea_root_password") as save_password,
            patch("selfad.routes.setup.hash_password", return_value="password-hash"),
        ):
            response = submit_setup(
                request=request,
                site_name="Example CTF",
                admin_username="organizer",
                admin_email="admin@example.test",
                admin_password="correct-password",
                admin_password_confirm="correct-password",
                change_title=False,
                remove_standard_logo=False,
                csrf_token="token",
                session=session,
            )

        self.assertEqual(response.status_code, 303)
        update_user.assert_called_once_with(
            settings,
            username="root",
            email="admin@example.test",
            password="correct-password",
        )
        authenticate.assert_called_once_with(
            settings,
            username="root",
            password="correct-password",
        )
        save_password.assert_called_once_with("correct-password")

        added_objects = session.add_all.call_args.args[0]
        admin = next(item for item in added_objects if isinstance(item, User))
        self.assertEqual(admin.username, "organizer")
        self.assertTrue(admin.is_admin)
        self.assertEqual(admin.gitea_user_id, 1)
        self.assertEqual(admin.gitea_username, "root")
        session.commit.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
