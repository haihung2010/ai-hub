#!/usr/bin/env bash
# ============================================================
#  Add MCP servers to hermes config
#  Chay: bash ~/ai-hub/scripts/03_add_mcp_servers.sh
# ============================================================
set -euo pipefail

CONFIG="$HOME/.hermes/config.yaml"

# Check if mcp_servers section exists
if ! grep -q "^mcp_servers:" "$CONFIG"; then
  echo "ERROR: mcp_servers section not found in $CONFIG"
  exit 1
fi

# Check if already added
if grep -q "arxiv:" "$CONFIG"; then
  echo "MCP servers already configured. Skipping."
  exit 0
fi

echo "Adding MCP servers to $CONFIG ..."

# Add after the filesystem_projects block (before the next top-level key)
cat >> "$CONFIG" << 'MCP_BLOCK'
  arxiv:
    command: uvx
    args:
    - arxiv-mcp-server
    connect_timeout: 60
    timeout: 120
  anyquery:
    command: anyquery
    args:
    - mcp
    connect_timeout: 30
    timeout: 60
  roundtable:
    command: uvx
    args:
    - roundtable-ai
    connect_timeout: 60
    timeout: 120
  aihub:
    url: "http://localhost:8000/mcp"
    connect_timeout: 30
    timeout: 60
MCP_BLOCK

echo "Done. Added: arxiv, anyquery, roundtable, aihub"
echo ""
echo "Restart hermes gateway to activate:"
echo "  hermes gateway restart"
echo ""
echo "Verify with:"
echo "  hermes mcp list"
