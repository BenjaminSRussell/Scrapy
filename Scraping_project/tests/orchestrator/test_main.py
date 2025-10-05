import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path to allow src imports
project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))

from src.orchestrator.main import _cleanup_temp_directory


class TestCleanupTempDirectory(unittest.TestCase):

    def setUp(self):
        # Create a temporary directory for the entire test
        self.test_dir = tempfile.mkdtemp()
        self.temp_dir = Path(self.test_dir) / 'temp'
        self.temp_dir.mkdir()

    def tearDown(self):
        # Clean up the test directory
        shutil.rmtree(self.test_dir)

    def test_cleanup_does_not_follow_symlink_to_delete_external_directory(self):
        """
        Verify that the cleanup function does not traverse symlinks and delete
        directories outside the temp folder.
        """
        # 1. Arrange
        # Create an external directory that should NOT be deleted
        external_dir = Path(self.test_dir) / 'external_safe_dir'
        external_dir.mkdir()
        (external_dir / 'safe_file.txt').touch()

        # Create a symlink inside the temp directory pointing to the external one
        symlink_to_external = self.temp_dir / 'symlink_to_danger'

        # Make the symlink "old" so the cleanup function tries to delete it
        two_days_ago = (datetime.now() - timedelta(days=2)).timestamp()

        # Create the symlink. Skip test if not supported.
        try:
            os.symlink(external_dir, symlink_to_external, target_is_directory=True)
        except (OSError, AttributeError, NotImplementedError) as e:
            # AttributeError for os.symlink not on old pythons/windows
            # OSError for permissions issues
            # NotImplementedError on some systems
            self.skipTest(f"Symlink creation failed: {e}")

        # Manually set the modification time of the symlink itself.
        # This can fail with permission errors in some environments.
        try:
            os.utime(symlink_to_external, (two_days_ago, two_days_ago), follow_symlinks=False)
        except OSError as e:
            self.skipTest(f"Could not set modification time on symlink: {e}")


        # 2. Act
        # Run the cleanup function with a 24-hour max age
        _cleanup_temp_directory(self.temp_dir, max_age_hours=24)

        # 3. Assert
        # The symlink inside the temp dir should be gone
        self.assertFalse(symlink_to_external.exists(), "Symlink should have been deleted")

        # The external directory and its contents MUST still exist
        self.assertTrue(external_dir.exists(), "External directory was deleted, but should not have been.")
        self.assertTrue((external_dir / 'safe_file.txt').exists(), "File in external directory was deleted.")


if __name__ == '__main__':
    unittest.main()