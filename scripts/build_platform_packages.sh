#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <version> <output-root>" >&2
  exit 2
fi

version="$1"
out_root="$2"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out_dir="$out_root/$version"

rm -rf "$out_dir"
mkdir -p \
  "$out_dir/vscode/ultrareview-vscode-$version/payload/github/agents" \
  "$out_dir/vscode/ultrareview-vscode-$version/payload/github/tools" \
  "$out_dir/claude-code-macos/ultrareview-claude-code-macos-$version/payload/claude/agents" \
  "$out_dir/claude-code-macos/ultrareview-claude-code-macos-$version/payload/claude/commands" \
  "$out_dir/claude-code-macos/ultrareview-claude-code-macos-$version/payload/tools"

rsync_excludes=(
  --exclude .git
  --exclude .venv
  --exclude .pytest_cache
  --exclude .ultrareview
  --exclude __pycache__
  --exclude '*.pyc'
  --exclude src/own_ultrareview.egg-info
)

rsync -a "${rsync_excludes[@]}" "$repo_root/" "$out_dir/vscode/ultrareview-vscode-$version/payload/github/tools/ultrareview/"
rsync -a "${rsync_excludes[@]}" "$repo_root/" "$out_dir/claude-code-macos/ultrareview-claude-code-macos-$version/payload/tools/ultrareview/"

cp "$repo_root/skills/ultrareview/references/ultrareview.agent.md" \
  "$out_dir/vscode/ultrareview-vscode-$version/payload/github/agents/ultrareview.agent.md"
cp "$repo_root/skills/ultrareview/references/claude-code-agent.md" \
  "$out_dir/claude-code-macos/ultrareview-claude-code-macos-$version/payload/claude/agents/ultrareview.md"
cp "$repo_root/skills/ultrareview/references/claude-code-command.md" \
  "$out_dir/claude-code-macos/ultrareview-claude-code-macos-$version/payload/claude/commands/my-ultrareview.md"

cat > "$out_dir/vscode/ultrareview-vscode-$version/INSTALL-VSCODE.md" <<EOF
# UltraReview VS Code Copilot Package $version

This package uses only visible folders. Finder will not hide the payload.

## Install

From the repository you want to review:

\`\`\`bash
/path/to/ultrareview-vscode-$version/install-vscode.sh .
\`\`\`

Or manually copy:

\`\`\`text
payload/github/agents/ultrareview.agent.md  ->  <repo>/.github/agents/ultrareview.agent.md
payload/github/tools/ultrareview/           ->  <repo>/.github/tools/ultrareview/
\`\`\`

Then install the runtime:

\`\`\`bash
cd <repo>/.github/tools/ultrareview
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
cd ../../..
./.github/tools/ultrareview/.venv/bin/own-ultrareview --help
\`\`\`

In VS Code Chat, select the \`UltraReview\` agent and ask it to review your branch.
EOF

cat > "$out_dir/vscode/ultrareview-vscode-$version/install-vscode.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
repo="${1:-.}"
base_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$repo/.github/agents" "$repo/.github/tools"
rsync -a "$base_dir/payload/github/agents/" "$repo/.github/agents/"
rsync -a "$base_dir/payload/github/tools/" "$repo/.github/tools/"
echo "Installed UltraReview VS Code agent into $repo/.github"
echo "Next: cd $repo/.github/tools/ultrareview && python3 -m venv .venv && . .venv/bin/activate && pip install -e ."
EOF
chmod +x "$out_dir/vscode/ultrareview-vscode-$version/install-vscode.sh"

cat > "$out_dir/claude-code-macos/ultrareview-claude-code-macos-$version/INSTALL-CLAUDE-CODE-MACOS.md" <<EOF
# UltraReview Claude Code macOS Package $version

This package uses only visible folders.

## Install

From the repository you want to review:

\`\`\`bash
/path/to/ultrareview-claude-code-macos-$version/install-claude-code-macos.sh .
\`\`\`

Or manually copy:

\`\`\`text
payload/claude/agents/ultrareview.md       ->  <repo>/.claude/agents/ultrareview.md
payload/claude/commands/my-ultrareview.md  ->  <repo>/.claude/commands/my-ultrareview.md
payload/tools/ultrareview/                 ->  <repo>/tools/ultrareview/
\`\`\`

Then install the runtime:

\`\`\`bash
cd <repo>/tools/ultrareview
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
cd ../..
./tools/ultrareview/.venv/bin/own-ultrareview --help
\`\`\`

In Claude Code, run \`/my-ultrareview origin/main\` or start the \`ultrareview\` project agent.
EOF

cat > "$out_dir/claude-code-macos/ultrareview-claude-code-macos-$version/install-claude-code-macos.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
repo="${1:-.}"
base_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$repo/.claude/agents" "$repo/.claude/commands" "$repo/tools"
rsync -a "$base_dir/payload/claude/agents/" "$repo/.claude/agents/"
rsync -a "$base_dir/payload/claude/commands/" "$repo/.claude/commands/"
rsync -a "$base_dir/payload/tools/" "$repo/tools/"
echo "Installed UltraReview Claude Code package into $repo"
echo "Next: cd $repo/tools/ultrareview && python3 -m venv .venv && . .venv/bin/activate && pip install -e ."
EOF
chmod +x "$out_dir/claude-code-macos/ultrareview-claude-code-macos-$version/install-claude-code-macos.sh"

(
  cd "$out_dir/vscode"
  zip -qr "$out_dir/ultrareview-vscode-$version.zip" "ultrareview-vscode-$version"
)
(
  cd "$out_dir/claude-code-macos"
  zip -qr "$out_dir/ultrareview-claude-code-macos-$version.zip" "ultrareview-claude-code-macos-$version"
)

printf '%s\n' \
  "$out_dir/ultrareview-vscode-$version.zip" \
  "$out_dir/ultrareview-claude-code-macos-$version.zip"
