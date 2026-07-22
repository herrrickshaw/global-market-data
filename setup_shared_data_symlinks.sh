#!/bin/bash
# Setup Shared Data Symlinks for New Branches
# Run this once when creating a new branch to link to centralized data
#
# Usage: bash setup_shared_data_symlinks.sh

set -e

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)

if [ $? -ne 0 ]; then
    echo "✗ Not in a Git repository"
    exit 1
fi

cd "$REPO_ROOT"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}Setup Shared Data Symlinks${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"
echo ""

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
echo -e "${YELLOW}Current branch: ${GREEN}$CURRENT_BRANCH${NC}"
echo ""

# Step 1: Enable symlink support
echo -e "${YELLOW}→ Enabling Git symlink support...${NC}"
git config core.symlinks true
echo -e "${GREEN}  ✓ Enabled${NC}"
echo ""

# Step 2: Check if shared_data exists
echo -e "${YELLOW}→ Checking for shared_data directory...${NC}"
if [ ! -d "shared_data" ]; then
    echo -e "${RED}  ✗ shared_data/ not found${NC}"
    echo ""
    echo "This branch needs shared_data/ to exist. You have two options:"
    echo ""
    echo "Option 1: Switch to main and run deduplication:"
    echo "  git checkout main"
    echo "  bash implement_deduplication.sh"
    echo "  git checkout $CURRENT_BRANCH"
    echo ""
    echo "Option 2: If shared_data exists on another branch:"
    echo "  git checkout main"
    echo "  git checkout $CURRENT_BRANCH"
    echo "  bash setup_shared_data_symlinks.sh"
    echo ""
    exit 1
fi
echo -e "${GREEN}  ✓ found${NC}"
echo ""

# Step 3: Remove old directories (if not symlinks)
echo -e "${YELLOW}→ Cleaning up old data directories...${NC}"
for dir in cache_seed fundamentals market_data screening_results; do
    if [ -d "$dir" ] && [ ! -L "$dir" ]; then
        echo "  Removing $dir/"
        rm -rf "$dir"
    fi
done
echo -e "${GREEN}  ✓ Cleaned${NC}"
echo ""

# Step 4: Create symlinks
echo -e "${YELLOW}→ Creating symlinks to shared_data...${NC}"

SYMLINKS=(
    "cache_seed:shared_data/cache_seed"
    "fundamentals:shared_data/fundamentals"
    "market_data:shared_data/market_data"
    "screening_results:shared_data/screening_results"
)

for pair in "${SYMLINKS[@]}"; do
    IFS=':' read -r link target <<< "$pair"

    if [ -L "$link" ]; then
        # Already a symlink
        echo -e "${GREEN}  ✓ $link (already exists)${NC}"
    elif [ -d "$link" ]; then
        # Directory exists, remove and create symlink
        rm -rf "$link"
        ln -s "$target" "$link"
        echo -e "${GREEN}  ✓ $link → $target${NC}"
    else
        # Create symlink
        ln -s "$target" "$link"
        echo -e "${GREEN}  ✓ $link → $target${NC}"
    fi
done

echo ""

# Step 5: Verify symlinks
echo -e "${YELLOW}→ Verifying symlinks...${NC}"
echo ""
ls -la cache_seed fundamentals market_data screening_results 2>/dev/null | head -4
echo ""

# Step 6: Test access
echo -e "${YELLOW}→ Testing data access...${NC}"

if python3 -c "import pandas as pd; df = pd.read_parquet('cache_seed/cleaned_long.parquet'); print(f'  ✓ Read data: {df.shape}')" 2>/dev/null; then
    echo -e "${GREEN}  ✓ Data access working${NC}"
else
    echo -e "${YELLOW}  ⚠ Could not read parquet (may not be installed)${NC}"
    echo "    Install with: pip install pandas pyarrow"
fi

echo ""

# Step 7: Summary
echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}✓ Setup Complete!${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"
echo ""

echo "Symlinks created:"
echo "  • cache_seed/          → ../shared_data/cache_seed/"
echo "  • fundamentals/        → ../shared_data/fundamentals/"
echo "  • market_data/         → ../shared_data/market_data/"
echo "  • screening_results/   → ../shared_data/screening_results/"
echo ""

echo "You can now:"
echo "  • Read data transparently: python -c \"import pandas as pd; pd.read_parquet('cache_seed/...')\""
echo "  • List files: ls cache_seed/"
echo "  • Check duplicates: python find_duplicates_standalone.py"
echo ""

echo "Git configuration:"
echo "  • core.symlinks = true (enabled)"
echo ""

echo -e "${YELLOW}Tip:${NC} Add to .git/hooks/post-checkout to auto-setup on branch changes"
echo ""
