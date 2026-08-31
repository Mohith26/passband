"""Runs everything. Exit code is non zero if anything failed, so CI can use it."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main():
    loader = unittest.TestLoader()
    suite = loader.discover(os.path.join(ROOT, "tests"), pattern="test_*.py", top_level_dir=ROOT)
    runner = unittest.TextTestRunner(verbosity=1, stream=sys.stdout)
    result = runner.run(suite)
    print("")
    print("tests run %d, failures %d, errors %d, skipped %d"
          % (result.testsRun, len(result.failures), len(result.errors), len(result.skipped)))
    for case, trace in result.failures + result.errors:
        print("")
        print("FAILED " + str(case))
        print(trace.rstrip())
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
