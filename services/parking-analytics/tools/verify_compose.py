"""Compatibility entry point for the complete isolated S4 integration check."""
import argparse
from verify_s4_integration import verify

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    verify(parser.parse_args().output)
