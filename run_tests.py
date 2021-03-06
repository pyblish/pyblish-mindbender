"""Test Pure Python functionality"""

import sys
import nose
import warnings

from nose_exclude import NoseExclude

warnings.filterwarnings("ignore", category=DeprecationWarning)

if __name__ == "__main__":
    argv = sys.argv[:]
    argv.extend([

        # Sometimes, files from Windows accessed
        # from Linux cause the executable flag to be
        # set, and Nose has an aversion to these
        # per default.
        "--exe",

        "--verbose",
        "--with-doctest",
        "--exclude-dir=mindbender/maya",
        "--exclude-dir=mindbender/nuke",
        "--exclude-dir=mindbender/houdini",

        # We can expect any vendors to
        # be well tested beforehand.
        "--exclude-dir=mindbender/vendor",
    ])

    nose.main(argv=argv,
              addplugins=[NoseExclude()])
