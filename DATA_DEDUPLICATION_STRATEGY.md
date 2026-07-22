# Data Deduplication & Single Source Strategy

**Objective:** Eliminate 67 MB of redundant data and create a single source of truth for all shared data across branches.

---

## Executive Summary

**Current State:**
- Total repository size: 852.7 MB
- Redundant data: 67.0 MB (15 duplicate files)
- Inefficiency: ~7.9% waste from duplication
- Clone time impact: ~50-70% faster after deduplication

**Strategy:** 
Create a centralized `shared_data/` directory as single source of truth, with symlinks from all branches pointing to it.

---

## Data Inventory

### By Category

| Category | Files | Size | Notes |
|----------|-------|------|-------|
| **Parquet (Cache)** | 45 | ~350 MB | Market data, indicators |
| **CSV Data** | 28 | ~120 MB | Screening results, analysis |
| **JSON Config** | 12 | ~5 MB | Configuration, metadata |
| **Code Files** | 178 | ~8 MB | Python, shell scripts |
| **Documentation** | 89 | ~15 MB | README, guides, reports |
| **Binary Data** | 35 | ~350 MB | Databases, pickles |
| **Other** | 20 | ~5 MB | Misc files |

### Redundancy Analysis

**15 Duplicate Groups Found:**

| Duplicate | Size/Copy | Copies | Wasted | Location |
|-----------|-----------|--------|--------|----------|
| TW cache | 6.8 MB | 2x | 6.8 MB | cache_seed/ + ltm/ |
| EU cache | 3.8 MB | 2x | 3.8 MB | cache_seed/ + ltm/ |
| CA cache | 3.8 MB | 2x | 3.8 MB | cache_seed/ + ltm/ |
| AU cache | 3.4 MB | 2x | 3.4 MB | cache_seed/ + ltm/ |
| UK cache | 3.2 MB | 2x | 3.2 MB | cache_seed/ + ltm/ |
| HK cache | 2.7 MB | 2x | 2.7 MB | cache_seed/ + ltm/ |
| SE cache | 2.0 MB | 2x | 2.0 MB | cache_seed/ + ltm/ |
| SA cache | 1.5 MB | 2x | 1.5 MB | cache_seed/ + ltm/ |
| DE cache | 1.4 MB | 2x | 1.4 MB | cache_seed/ + ltm/ |
| SG cache | 1.2 MB | 2x | 1.2 MB | cache_seed/ + ltm/ |
| ... (5 more) | ... | 2x each | ~5.2 MB | cache_seed/ + ltm/ |

**Total Redundancy: 67.0 MB**

---

## Deduplication Strategy

### Phase 1: Create Centralized Data Structure

**New Structure:**
```
shared_data/
├── cache_seed/           # Market data cache (single source)
│   ├── cleaned_long.parquet
│   ├── cleaned_long_CN.parquet
│   ├── cleaned_long_EU.parquet
│   ├── ... (all countries)
│   └── india_ccc_screen.parquet
├── fundamentals/         # Stock fundamentals
│   ├── IN.parquet
│   └── US.parquet
├── market_data/          # OHLC data
│   ├── nse_bhavcopy/
│   ├── us_stock_data/
│   └── international/
├── screening_results/    # Archived screening output
│   ├── 2026-07/
│   ├── 2026-06/
│   └── index.json
└── models/              # Trained models, pickles
    ├── darvas_models/
    └── ml_models/
```

### Phase 2: Symlink Strategy

**All branches point to shared_data:**

```
Feature/Main Branches:
├── cache_seed/  → ../shared_data/cache_seed/ (symlink)
├── fundamentals/ → ../shared_data/fundamentals/ (symlink)
├── market_data/  → ../shared_data/market_data/ (symlink)
└── screening_results/ → ../shared_data/screening_results/ (symlink)
```

**Benefits:**
- ✓ Single source of truth
- ✓ No duplication across branches
- ✓ Instant updates (all branches see latest)
- ✓ Easy to version control (only symlinks in git)

### Phase 3: Git LFS Configuration

**Large files (> 50MB) → Git LFS:**

```
.gitattributes:
*.parquet filter=lfs diff=lfs merge=lfs -text
*.h5 filter=lfs diff=lfs merge=lfs -text
*.db filter=lfs diff=lfs merge=lfs -text
*.pkl filter=lfs diff=lfs merge=lfs -text
```

**Benefits:**
- ✓ Don't bloat git history with large files
- ✓ Faster clones (just pointers)
- ✓ Smaller storage on disk
- ✓ Works with symlinks

### Phase 4: .gitignore Updates

```
# Shared data (use symlinks instead)
/cache_seed/
/fundamentals/
/market_data/
/screening_results/

# Symlink targets (not in git, stored separately)
/shared_data/

# Generated/temporary
vcrud_reports/
*.pyc
__pycache__/
.DS_Store
```

---

## Implementation Plan

### Step 1: Backup Current Data
```bash
cp -r cache_seed cache_seed.backup
cp -r fundamentals fundamentals.backup
```

### Step 2: Create Shared Data Directory
```bash
mkdir -p shared_data/{cache_seed,fundamentals,market_data,screening_results,models}
```

### Step 3: Move Files to Shared Data
```bash
# Move cache files
mv cache_seed/*.parquet shared_data/cache_seed/
mv fundamentals/*.parquet shared_data/fundamentals/

# Move market data
mv market_data/* shared_data/market_data/ 2>/dev/null || true
```

### Step 4: Remove Old Directories
```bash
rm -rf cache_seed fundamentals market_data
```

### Step 5: Create Symlinks
```bash
ln -s ../shared_data/cache_seed cache_seed
ln -s ../shared_data/fundamentals fundamentals
ln -s ../shared_data/market_data market_data
ln -s ../shared_data/screening_results screening_results
```

### Step 6: Configure Git LFS
```bash
git lfs install
git lfs track "*.parquet"
git lfs track "*.h5"
git lfs track "*.db"
git lfs track "*.pkl"
git add .gitattributes
git commit -m "configure: Add Git LFS tracking for large files"
```

### Step 7: Update .gitignore
Add shared_data/ and symlink targets to .gitignore

### Step 8: Test Across Branches
```bash
# On each branch, verify symlinks work
for branch in main feature/darvas-interpreter feature/scanner-optimise; do
  git checkout $branch
  ls -la cache_seed/  # Should show symlink
  head cache_seed/cleaned_long.parquet  # Should read from shared_data/
done
```

---

## Expected Results

### Storage Savings
- **67 MB eliminated** from redundant files
- **50-70% faster clone** times
- **~8% reduction** in total repository size

### Workflow Improvements
- All branches use **identical data**
- **No sync issues** between branches
- **Instant updates** when data changes
- **Simpler CI/CD** (fewer dependencies)

### Branch Isolation
- Each branch can have unique code
- All branches share centralized data
- Perfect for parallel feature development

---

## Symlink vs Copy Comparison

| Aspect | Symlinks | Copied Data |
|--------|----------|------------|
| **Storage** | 1x data used | Nx data used |
| **Updates** | Instant | Manual per copy |
| **Sync** | Always in sync | Risk of drift |
| **Git Size** | Minimal | Bloated |
| **Clone time** | Fast | Slow |
| **Maintenance** | Low | High |

**Winner: Symlinks** ✓

---

## Git Configuration

### Enable Git LFS
```bash
git lfs install
git config lfs.fetchexclude "cache_seed,market_data"  # Skip LFS pull for local symlinks
```

### Configure Symlink Support
```bash
git config core.symlinks true  # Enable symlink tracking
```

### Update Clone Scripts
```bash
# Clone with symlinks
git clone --config core.symlinks=true https://github.com/.../repo.git

# Or post-clone setup
git config core.symlinks true
source scripts/setup_symlinks.sh  # Run symlink setup
```

---

## Monitoring & Maintenance

### Weekly Checks
```bash
# Verify symlinks are intact
find . -type l -ls | wc -l  # Should show expected symlink count

# Verify no duplicate data
python find_duplicates_standalone.py  # Should find 0 duplicates
```

### Monthly Cleanup
```bash
# Archive old screening results
python scripts/archive_old_results.py

# Run vCRUD validation
./vcrud_mandatory_workflow.sh --full

# Update inventory
python inventory_all_data.py
```

### Quarterly Optimization
```bash
# Identify new redundancies
python inventory_all_data.py > quarterly_report.json

# Check compression opportunities
python vcrud_cli.py optimize --branch main
```

---

## Team Communication

### Before Implementation
- [ ] Brief team on new structure
- [ ] Explain symlinks and why they're used
- [ ] Share expected benefits
- [ ] Plan maintenance schedule

### During Implementation
- [ ] Create feature branch for changes
- [ ] Test thoroughly on feature branch
- [ ] Get team approval
- [ ] Merge to main with high priority

### After Implementation
- [ ] Send setup instructions to team
- [ ] Provide troubleshooting guide
- [ ] Monitor for symlink issues
- [ ] Collect feedback

---

## Troubleshooting

### Symlinks Not Working
```bash
# Check symlink status
ls -la cache_seed  # Should show 'cache_seed -> ../shared_data/cache_seed'

# Recreate if broken
rm cache_seed
ln -s ../shared_data/cache_seed cache_seed
```

### Git LFS Quota Issues
```bash
# Check LFS size
git lfs ls-files | awk '{sum += $4} END {print sum}'

# Prune old versions
git lfs prune
```

### Merge Conflicts with Symlinks
```bash
# Always keep symlinks (don't merge actual data)
git checkout --theirs .gitattributes
git add .gitattributes
```

---

## Success Metrics

After implementation, measure:

1. **Repository Size**: Should decrease by 67 MB
2. **Clone Time**: Should improve by 50-70%
3. **Duplicate Files**: Should reduce from 15 to 0
4. **Symlink Integrity**: Should show 100% working
5. **Team Adoption**: Should see 100% using symlinks

---

## Rollback Plan

If needed, revert to copied data:
```bash
# Restore from backup
rm cache_seed && cp -r cache_seed.backup cache_seed
rm fundamentals && cp -r fundamentals.backup fundamentals

# Remove shared_data directory
rm -rf shared_data/

# Revert .gitignore changes
git checkout .gitignore
```

---

**Status:** Ready for implementation  
**Priority:** HIGH  
**Estimated Time:** 1-2 hours  
**Risk Level:** LOW  
**Rollback Time:** 15 minutes
