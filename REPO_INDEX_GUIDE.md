# Repository Index Guide

**Easy cross-referencing between all branches in the repository**

---

## What is This?

A comprehensive index of your entire repository that shows:
- All 52 branches and their purpose
- What files are unique to each branch
- Data dependencies (which branches need cache_seed, fundamentals, etc.)
- Merge strategies and integration paths
- Feature tracking across branches

---

## Files Generated

### 1. `REPO_INDEX.md` (Human-Readable)
Browse this file to understand the repository structure. See:
- Overview table of all branches
- Features organized by branch
- Unique files per branch
- Data dependencies
- Merge strategies

### 2. `REPO_INDEX.json` (Machine-Readable)
Use this in scripts or tools to:
- Query branch metadata
- Analyze dependencies
- Build CI/CD workflows
- Automate cross-branch operations

---

## Quick Navigation

### Find a Branch
```bash
grep -A5 "feature/your-name" REPO_INDEX.md
# Shows: purpose, files, dependencies
```

### Find Which Branches Use Cache Data
```bash
grep "cache_seed" REPO_INDEX.md
# All branches that depend on market cache
```

### Find Unique Files on a Branch
Search for "### branch-name" in REPO_INDEX.md

---

## Current Repository Structure

**Main Branch:** `main` (52 total branches)

**Branch Categories:**
- **Feature branches:** 35+ active features
- **Archive branches:** 9 archived/legacy
- **Maintenance:** dashboard, mailer
- **Experiment:** vcrud, safety, performance

---

## Index Details

### 1. Branch Overview Table
Shows every branch with:
- **Purpose:** What the branch is for
- **Files:** Number of files on branch
- **Latest:** Most recent commit message
- **Dependencies:** What data it needs

### 2. Features by Branch
Organized by feature type:
- Dashboard features
- Data screening features
- API features
- Pipeline features
- ADR (Architecture Decision Record) implementations

### 3. Unique Files
Files that only exist on specific branches:
- New experimental code
- Branch-specific configurations
- Feature-specific implementations

### 4. Data Dependencies
Which branches need:
- `cache_seed` - Market data cache
- `fundamentals` - Stock fundamental data
- `market_data` - OHLC data
- `ml_models` - Trained models

### 5. Merge Strategy
Shows merge paths:
- `feature/*` → `develop` → `main`
- `hotfix/*` → `main` → `develop`

---

## How to Use This Index

### Planning a Merge
1. Check REPO_INDEX.md for target branch
2. See what's unique on your branch
3. Check dependencies
4. Review merge targets
5. Proceed with confidence

### Finding Related Branches
```bash
# All branches using cache_seed
grep "cache_seed" REPO_INDEX.md

# All feature/* branches
grep "^| \`feature/" REPO_INDEX.md
```

### Understanding Branch Purpose
```bash
# Find branch purpose
grep "your-branch" REPO_INDEX.md | head -1
# Shows: "Feature: description" or "Fix: description"
```

### Tracking Dependencies
```bash
# What does feature/darvas need?
grep -A1 "feature/darvas" REPO_INDEX.md
```

---

## Keeping Index Updated

The index is generated from live Git data:

```bash
# Regenerate index (5-10 seconds)
python3 generate_repo_index.py

# Commit updated index
git add REPO_INDEX.json REPO_INDEX.md
git commit -m "Update repository index"
```

**Recommended:** Run after major branch activity:
- New feature branches created
- Branches merged to main
- Data structure changes
- Quarterly reviews

---

## Index Contents Explained

### Branch Info (in table)
- **Branch:** Name (clickable to branch)
- **Purpose:** What it's for
- **Files:** Number of tracked files
- **Latest:** Most recent change
- **Dependencies:** Data it needs

### Unique Files (by branch)
Top 5 files that exist only on that branch
- Shows what differentiates the branch
- Helps identify feature scope

### Features by Type
Organized categories:
- Screening features (batch-a, batch-b, batch-c)
- Dashboard (schedule, harden)
- Pipeline (daily, morning-mailer)
- ADRs (decisions 16-20)

### Dependencies
Shows data requirements:
- If needs cache_seed: market data analysis branch
- If needs fundamentals: stock fundamental data
- If needs models: ML features branch

---

## Examples

### Example 1: Planning Merge from feature/darvas

```bash
# Check REPO_INDEX.md:
grep "feature/darvas" REPO_INDEX.md

# Output shows:
# - Purpose: Feature: darvas-enhancement
# - Files: 42
# - Dependencies: cache_seed
# - Merge to: develop, main

# Decision: This branch can merge to main
# But needs cache_seed in place first
```

### Example 2: Finding All Dashboard Features

```bash
grep -E "dashboard|Dashboard" REPO_INDEX.md

# Shows:
# - feature/dashboard-schedule
# - feature/dashboard-schedule-harden
# - dashboard/2026-07-02
```

### Example 3: Understanding Repository Load

```bash
# From index summary:
# Main branch: main
# Total branches: 52
# Total unique files: 23
# Features tracked: 35

# Interpretation:
# - 52 total branches (active + archive)
# - 23 files unique to specific branches
# - 35 active features being tracked
```

---

## Advanced Usage

### Query JSON Index

```python
import json

with open('REPO_INDEX.json') as f:
    index = json.load(f)

# Find all branches using cache_seed
for branch, info in index['branches'].items():
    if 'cache_seed' in info.get('dependencies', []):
        print(f"{branch}: {info['purpose']}")
```

### CI/CD Integration

```bash
# Get main branch
MAIN=$(jq -r '.main_branch' REPO_INDEX.json)

# Get all branches with cache_seed dependency
jq -r '.branches[] | select(.dependencies[] | select(. == "cache_seed")) | .name' REPO_INDEX.json
```

### Automated Notifications

```bash
# Check if index needs refresh
git status REPO_INDEX.json

# If changed, update and notify
if [ -n "$(git status --short REPO_INDEX.json)" ]; then
  echo "Repository structure changed - update CI/CD dependencies"
fi
```

---

## Structure Legend

```
Branch Overview
├── Name: Branch identifier
├── Purpose: Feature/fix/merge purpose
├── Files: Count of tracked files
├── Latest: Newest commit
└── Dependencies: What data is needed

Features by Branch
├── Feature category
├── Associated branches
└── How they relate

Data Dependencies
├── cache_seed: Market data
├── fundamentals: Stock data
├── market_data: OHLC data
└── ml_models: ML artifacts

Merge Strategy
├── → develop (staging)
├── → main (production)
└── Order of integration
```

---

## Maintenance Schedule

**Weekly:**
- Run when major features merge
- Keep index fresh in git

**Monthly:**
- Full regeneration
- Archive old branches
- Update documentation

**Quarterly:**
- Strategic review
- Remove stale branches
- Restructure if needed

---

## Integration with Other Tools

### With vCRUD
```bash
# Verify no duplicates
python find_duplicates_standalone.py

# Update index
python3 generate_repo_index.py

# Verify consistency
python vcrud_cli.py duplicates
```

### With New Branch Setup
```bash
# Create branch
git checkout -b feature/new-work

# Check index for dependencies
grep "feature/new-work" REPO_INDEX.md

# Setup shared data if needed
bash setup_shared_data_symlinks.sh
```

### With CI/CD
```bash
# In .github/workflows/ci.yml
- name: Update repository index
  run: python3 generate_repo_index.py
  
- name: Verify index
  run: |
    if [ -n "$(git status --short REPO_INDEX.*)" ]; then
      echo "Repository structure changed"
      git add REPO_INDEX.*
      git commit -m "Update repository index"
    fi
```

---

## Benefits

✅ **Easy Navigation** - Understand repository at a glance  
✅ **Cross-Reference** - Find related branches instantly  
✅ **Dependency Tracking** - Know what data each branch needs  
✅ **Merge Planning** - Visualize integration paths  
✅ **CI/CD Ready** - JSON format for automation  
✅ **Human Readable** - Markdown for quick browsing  
✅ **Always Current** - Quick to regenerate (5-10 sec)  

---

## Commands Cheat Sheet

```bash
# Generate index
python3 generate_repo_index.py

# Browse markdown index
cat REPO_INDEX.md | less

# Query JSON index
jq '.main_branch' REPO_INDEX.json

# Find branch info
grep "branch-name" REPO_INDEX.md

# Check for cache dependencies
grep "cache_seed" REPO_INDEX.md

# List all features
jq '.feature_matrix | keys' REPO_INDEX.json

# Update and commit
python3 generate_repo_index.py && git add REPO_INDEX.* && git commit -m "Update index"
```

---

## See Also

- `REPO_INDEX.md` - Human-readable index
- `REPO_INDEX.json` - Machine-readable data
- `generate_repo_index.py` - Index generator script
- `DATA_STRUCTURE_GUIDE.md` - Data organization
- `NEW_BRANCH_DATA_SETUP.md` - Setting up new branches

