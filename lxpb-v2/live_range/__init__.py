import os
import sys

_PP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "patterns_pure")
if _PP not in sys.path:
    sys.path.insert(0, _PP)       # find_zigzag: the repo's only zigzag
