import os
import sys

DEBUG = sys.flags.dev_mode or bool(os.getenv("DEBUG"))
