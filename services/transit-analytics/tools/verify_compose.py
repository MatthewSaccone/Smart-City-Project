"""Authentik HTTP verification of a configured local S6 deployment.
No containers or data are modified. See docs/Authentik-Migration.md.
"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/auth"))
from verify_deployment import verify as verify_deployment

def verify(output=None):
    return verify_deployment(["s6"], output)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    verify(parser.parse_args().output)
