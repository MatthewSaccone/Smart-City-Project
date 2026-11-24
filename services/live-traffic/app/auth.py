"""Compatibility alias for the common resource-server implementation."""
import sys
from shared import auth
sys.modules[__name__] = auth
