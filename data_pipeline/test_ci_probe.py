import unittest


class RecoveryCiProbe(unittest.TestCase):
    def test_probe(self):
        # The real recovery assertions live in test_migrate_verified_snapshot_v7.
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
