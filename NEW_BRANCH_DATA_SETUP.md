# Setting Up Data References for New Branches

**How to make any new branch access centralized shared data**

---

## Quick Start (30 seconds)

When creating a new branch, just run:

```bash
# Create your new branch
git checkout -b feature/your-feature

# Run the setup script (one time)
source scripts/setup_shared_data_symlinks.sh

# Done! You now have access to all shared data
```

---

## What Happens Behind the Scenes

### Step 1: Create New Branch
```bash
git checkout -b feature/your-new-feature
```

At this point, your branch inherits the repository structure from main, which already has symlinks.

### Step 2: Verify Symlinks
```bash
ls -la cache_seed/
# Output should show:
# cache_seed -> ../shared_data/cache_seed/
```

If symlinks don't appear, Git needs to be configured:

```bash
git config core.symlinks true
```

### Step 3: Access Shared Data
```bash
# Read from shared data (works immediately)
python -c "import pandas as pd; df = pd.read_parquet('cache_seed/cleaned_long.parquet')"

# All symlinks work automatically
ls cache_seed/              # Lists files from shared_data/cache_seed/
ls fundamentals/            # Lists files from shared_data/fundamentals/
ls market_data/             # Lists files from shared_data/market_data/
```

---

## Method 1: Automatic Setup (Recommended)

### For Local Development

**Setup once:**
```bash
git clone --config core.symlinks=true <repo-url>
cd repo
bash setup_shared_data_symlinks.sh
```

**For each new branch:**
```bash
git checkout -b feature/your-feature
# Symlinks already there - ready to use!
```

### Script: `setup_shared_data_symlinks.sh`

```bash
#!/bin/bash
# One-time setup for data symlinks in new branches

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

# Enable symlink support
git config core.symlinks true

# Create symlinks to shared data
ln -sf shared_data/cache_seed cache_seed
ln -sf shared_data/fundamentals fundamentals
ln -sf shared_data/market_data market_data
ln -sf shared_data/screening_results screening_results

echo "✓ Data symlinks ready"
ls -la cache_seed/  # Verify
```

---

## Method 2: Manual Setup

If you prefer manual setup or need troubleshooting:

### Step 1: Enable Symlink Support
```bash
git config core.symlinks true
```

### Step 2: Verify shared_data Directory Exists
```bash
ls -la shared_data/
# Should show:
# cache_seed/
# fundamentals/
# market_data/
# screening_results/
# models/
```

If `shared_data/` doesn't exist, you need to:
1. Check out `main` branch
2. Run `bash implement_deduplication.sh`
3. Return to your branch

### Step 3: Create Symlinks Manually
```bash
# Remove any existing directories
rm -rf cache_seed fundamentals market_data screening_results

# Create symlinks
ln -s shared_data/cache_seed cache_seed
ln -s shared_data/fundamentals fundamentals
ln -s shared_data/market_data market_data
ln -s shared_data/screening_results screening_results

# Verify
ls -la | grep "^l"  # Should show all 4 symlinks
```

### Step 4: Verify Access
```bash
# Test reading from shared data
head cache_seed/cleaned_long.parquet

# Should work without errors
```

---

## Method 3: Programmatic (Python)

If you're writing scripts:

```python
import os
import sys

def setup_data_symlinks():
    """Setup data symlinks in current branch"""
    
    repo_root = os.popen('git rev-parse --show-toplevel').read().strip()
    os.chdir(repo_root)
    
    # Enable symlinks
    os.system('git config core.symlinks true')
    
    # Create symlinks
    symlink_pairs = [
        ('cache_seed', 'shared_data/cache_seed'),
        ('fundamentals', 'shared_data/fundamentals'),
        ('market_data', 'shared_data/market_data'),
        ('screening_results', 'shared_data/screening_results'),
    ]
    
    for link, target in symlink_pairs:
        if os.path.exists(link) and not os.path.islink(link):
            os.system(f'rm -rf {link}')
        
        if not os.path.exists(link):
            os.symlink(target, link)
            print(f'✓ Created {link} -> {target}')
        else:
            print(f'✓ {link} already exists')

if __name__ == '__main__':
    setup_data_symlinks()
```

Use it:
```bash
python setup_data_symlinks.py
```

---

## Method 4: Git Hooks (Automatic on Every Branch Change)

**Setup once - then automatic for all branches:**

Create `.git/hooks/post-checkout`:

```bash
#!/bin/bash
# Auto-setup symlinks when switching branches

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

# Enable symlinks
git config core.symlinks true

# Create symlinks if missing
for link in cache_seed fundamentals market_data screening_results; do
    if [ ! -L "$link" ] && [ ! -d "$link" ]; then
        ln -s shared_data/$link $link
        echo "✓ Created symlink: $link"
    fi
done
```

Make it executable:
```bash
chmod +x .git/hooks/post-checkout
```

Now every time you switch branches, symlinks are automatically created!

---

## Cloning a New Repository

If someone clones the repo fresh:

```bash
# Clone with symlink support enabled
git clone --config core.symlinks=true <repo-url>

cd repo

# Enable symlinks (persistent)
git config core.symlinks true

# Create symlinks
bash setup_shared_data_symlinks.sh

# Verify
ls cache_seed/  # Should list files from shared_data/
```

---

## Common Scenarios

### Scenario 1: New Feature Branch

```bash
# From main
git checkout main

# Create new branch
git checkout -b feature/new-screening-method

# Symlinks automatically inherited from main
ls cache_seed/  # Works!

# You can immediately use the data
python screening_script.py
```

### Scenario 2: From Another Feature Branch

```bash
# From feature/darvas-interpreter
git checkout feature/darvas-interpreter

# Create new branch based on it
git checkout -b feature/darvas-enhancement

# Symlinks inherited (points to same shared_data/)
python analyze_darvas.py
```

### Scenario 3: Team Member Cloning for First Time

```bash
# Clone repo
git clone --config core.symlinks=true https://...

# Setup data symlinks
cd repo
bash setup_shared_data_symlinks.sh

# Create their feature branch
git checkout -b feature/their-work

# Ready to work
python their_script.py
```

### Scenario 4: Switching Between Branches

```bash
# Git automatically handles symlink switching
git checkout main
ls cache_seed/  # Points to main's shared_data

git checkout feature/branch-a
ls cache_seed/  # Points to same shared_data (all branches share)

# All branches see identical data!
```

---

## Troubleshooting

### Problem: Symlinks Not Working

**Diagnosis:**
```bash
ls -la cache_seed/
# If shows regular directory (not arrow), symlinks not set up
```

**Solution:**
```bash
git config core.symlinks true
bash setup_shared_data_symlinks.sh
```

### Problem: "Permission Denied" on Data Access

**Diagnosis:**
```bash
ls cache_seed/
# error: Permission denied
```

**Solution:**
```bash
# Fix permissions
chmod -R 755 shared_data/

# Or re-create symlinks
rm cache_seed
ln -s shared_data/cache_seed cache_seed
```

### Problem: Symlinks Showing as Files in Git

**Diagnosis:**
```bash
git status
# Shows cache_seed/ as modified (should show nothing)
```

**Solution:**
```bash
# Re-enable symlinks
git config core.symlinks true

# Reset to branch state
git reset --hard HEAD

# Verify
ls -la cache_seed/  # Should show arrow
```

### Problem: shared_data Directory Missing

**Diagnosis:**
```bash
ls shared_data/
# No such file or directory
```

**Solution:**
```bash
# Check if you're on main branch
git branch
# If not on main, switch to it
git checkout main

# If still missing, run deduplication
bash implement_deduplication.sh

# Return to your branch
git checkout your-branch
```

---

## Verification Checklist

After setting up a new branch, verify:

```bash
# 1. Check symlinks exist
ls -la | grep "^l"
# Should show: cache_seed -> shared_data/cache_seed/, etc.

# 2. Verify access
ls cache_seed/ | head
# Should list parquet files

# 3. Test reading data
python -c "import pandas as pd; print(pd.read_parquet('cache_seed/cleaned_long.parquet').shape)"
# Should print shape without errors

# 4. Check git config
git config core.symlinks
# Should print: true

# 5. Run vCRUD check
python find_duplicates_standalone.py
# Should find 0 duplicates (all using same shared_data)
```

---

## For CI/CD Pipelines

If using GitHub Actions or other CI/CD:

```yaml
name: Setup and Run Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
        with:
          # Enable symlink support
          token: ${{ secrets.GITHUB_TOKEN }}

      - name: Setup data symlinks
        run: |
          git config core.symlinks true
          bash setup_shared_data_symlinks.sh

      - name: Run tests
        run: python -m pytest tests/
```

---

## For Docker/Containers

```dockerfile
FROM python:3.9

WORKDIR /app

# Clone with symlink support
RUN git clone --config core.symlinks=true <repo-url> .

# Setup symlinks
RUN git config core.symlinks true && \
    bash setup_shared_data_symlinks.sh

# Data is ready for use
RUN python screening_script.py
```

---

## Git Configuration (Persistent)

Make symlink support the default for this repo:

```bash
# Local config (this repo only)
git config core.symlinks true

# Global config (all repos)
git config --global core.symlinks true

# Verify
git config core.symlinks
```

Add to `.git/config`:
```ini
[core]
    symlinks = true
```

---

## Team Best Practices

### For Team Lead (Main Branch)
1. Ensure `shared_data/` exists and is populated
2. Ensure symlinks are committed to main
3. Run `implement_deduplication.sh` once
4. Share `setup_shared_data_symlinks.sh` with team

### For Team Members (Feature Branches)
1. Clone with `--config core.symlinks=true`
2. Run `setup_shared_data_symlinks.sh`
3. Create feature branch
4. Data is ready - start coding

### For New Team Members (Onboarding)
1. Read this guide
2. Clone repo with symlink support
3. Run setup script
4. Run vCRUD check: `python find_duplicates_standalone.py`
5. Data should be accessible

---

## One-Liner Setup

```bash
git clone --config core.symlinks=true <url> && cd repo && git config core.symlinks true && bash setup_shared_data_symlinks.sh && python find_duplicates_standalone.py
```

---

## Summary

**For any new branch:**

1. **Automatic:** Symlinks inherit from main via Git
2. **Verify:** `ls -la cache_seed/` should show arrow
3. **If missing:** Run `bash setup_shared_data_symlinks.sh`
4. **Test:** `python find_duplicates_standalone.py` should find 0 duplicates
5. **Ready:** Use shared data transparently in your code

**No code changes needed - data access is transparent!**

---

## See Also

- `DATA_STRUCTURE_GUIDE.md` - How the centralized structure works
- `DATA_DEDUPLICATION_STRATEGY.md` - Full strategy and rationale
- `implement_deduplication.sh` - One-time setup script
- `setup_shared_data_symlinks.sh` - Per-branch symlink setup
