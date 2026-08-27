#!/usr/bin/env python3
"""入口脚本。MCP client 启动时的工作目录不确定，这里显式把项目根加进 sys.path。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from canvas_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main()
