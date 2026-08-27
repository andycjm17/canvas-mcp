#!/usr/bin/env python3
"""Entry point. MCP clients start the server from an unpredictable working
directory, so put the project root on sys.path explicitly."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from canvas_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main()
