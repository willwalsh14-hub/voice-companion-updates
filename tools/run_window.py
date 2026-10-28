import sys, unittest
import real_window_tests
real_window_tests.real_window = lambda cls: cls
names = sys.argv[1:]
suite = unittest.defaultTestLoader.loadTestsFromNames(names)
r = unittest.TextTestRunner(verbosity=1).run(suite)
sys.exit(not r.wasSuccessful())
