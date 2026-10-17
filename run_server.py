#!/usr/bin/env python3
"""Entry point that works from any working directory (used by Claude Desktop / Claude Code configs).
   python run_server.py            stdio
   python run_server.py --http     streamable HTTP"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from server.app import mcp
if __name__ == "__main__":
    mcp.run("streamable-http" if "--http" in sys.argv else "stdio")
