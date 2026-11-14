#!/bin/bash
# Setup git hooks for the repository

set -e

echo "Setting up git hooks..."
# A relative path uses each checkout's hooks instead of pinning a linked worktree.
git config core.hooksPath .githooks
echo "Git hooks configured to use .githooks directory"
echo "Done!"
