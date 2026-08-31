import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

loader = unittest.TestLoader()
total = 0
for name in sorted(os.listdir(os.path.join(ROOT, "tests"))):
    if not name.startswith("test_") or not name.endswith(".py"):
        continue
    suite = loader.discover(os.path.join(ROOT, "tests"), pattern=name, top_level_dir=ROOT)
    n = suite.countTestCases()
    total += n
    print("%-28s %d" % ("tests/" + name, n))
print("%-28s %d" % ("total", total))
