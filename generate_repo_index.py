#!/usr/bin/env python3
"""
Repository Index Generator
Creates comprehensive index of all branches with cross-references

Generates:
1. REPO_INDEX.json - Machine-readable index
2. REPO_INDEX.md - Human-readable markdown
3. Branch metadata and dependencies
"""

import json
import subprocess
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import re

class RepositoryIndexGenerator:
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path)
        self.branches = {}
        self.index = {
            'generated': datetime.now().isoformat(),
            'repository': str(repo_path),
            'main_branch': None,
            'branches': {},
            'dependencies': {},
            'file_index': defaultdict(list),
            'feature_matrix': {}
        }

    def get_all_branches(self) -> list:
        """Get all local and remote branches"""
        result = subprocess.run(
            ['git', 'branch', '-a'],
            cwd=self.repo_path,
            capture_output=True,
            text=True
        )
        branches = [
            b.strip().lstrip('* ').replace('remotes/origin/', '')
            for b in result.stdout.split('\n')
            if b.strip() and 'HEAD' not in b
        ]
        return list(set(branches))

    def get_branch_info(self, branch: str) -> dict:
        """Get metadata for a branch"""
        try:
            # File count
            result = subprocess.run(
                ['git', 'ls-tree', '-r', '--name-only', branch],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=5
            )
            files = [f for f in result.stdout.split('\n') if f and '.git' not in f]

            # Latest commit
            result = subprocess.run(
                ['git', 'log', '-1', '--format=%H|%s|%ai|%an', branch],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.stdout:
                commit_hash, message, date, author = result.stdout.strip().split('|')
            else:
                commit_hash, message, date, author = '', '', '', ''

            # Branch purpose from filename/commit
            purpose = self._determine_branch_purpose(branch, message)

            # Unique files
            unique_files = self._get_unique_files(branch, files)

            return {
                'name': branch,
                'file_count': len(files),
                'latest_commit': {
                    'hash': commit_hash[:8],
                    'message': message,
                    'date': date,
                    'author': author
                },
                'purpose': purpose,
                'unique_files': unique_files,
                'file_types': self._analyze_file_types(files),
                'has_cache_data': 'cache_seed' in files or 'fundamentals' in files,
                'has_code': any(f.endswith('.py') for f in files),
                'has_models': any('model' in f.lower() for f in files),
                'dependencies': self._find_dependencies(files)
            }
        except Exception as e:
            print(f"Error reading branch {branch}: {e}")
            return {'name': branch, 'error': str(e)}

    def _determine_branch_purpose(self, branch: str, commit_msg: str) -> str:
        """Determine branch purpose from name and commit"""
        if 'feature/' in branch:
            feature = branch.split('feature/')[-1]
            return f"Feature: {feature}"
        elif 'fix/' in branch:
            return f"Bug fix: {branch.split('fix/')[-1]}"
        elif 'refactor/' in branch:
            return f"Refactor: {branch.split('refactor/')[-1]}"
        elif 'hotfix/' in branch:
            return f"Hotfix: {branch.split('hotfix/')[-1]}"
        elif 'release/' in branch:
            return f"Release: {branch.split('release/')[-1]}"
        elif branch == 'main':
            return "Main production branch"
        elif branch == 'develop':
            return "Development integration branch"
        else:
            # Try to infer from commit message
            if 'feat:' in commit_msg:
                return f"Feature: {commit_msg.split('feat:')[-1].split()[0]}"
            elif 'fix:' in commit_msg:
                return f"Fix: {commit_msg.split('fix:')[-1].split()[0]}"
            else:
                return branch

    def _get_unique_files(self, branch: str, files: list) -> list:
        """Get files unique to this branch"""
        # Compare with main branch
        try:
            result = subprocess.run(
                ['git', 'ls-tree', '-r', '--name-only', 'main'],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=5
            )
            main_files = set(result.stdout.split('\n'))
            branch_files = set(files)
            unique = list(branch_files - main_files)
            return unique[:5]  # Top 5
        except:
            return []

    def _analyze_file_types(self, files: list) -> dict:
        """Analyze file types in branch"""
        types = defaultdict(int)
        for f in files:
            ext = Path(f).suffix or 'no_ext'
            types[ext] += 1
        return dict(sorted(types.items(), key=lambda x: x[1], reverse=True))

    def _find_dependencies(self, files: list) -> list:
        """Find what this branch depends on"""
        deps = []
        if 'cache_seed' in files or any('cache' in f for f in files):
            deps.append('cache_seed')
        if 'fundamentals' in files or any('fundamental' in f for f in files):
            deps.append('fundamentals')
        if 'market_data' in files or any('ohlc' in f or 'market' in f for f in files):
            deps.append('market_data')
        if any('model' in f.lower() for f in files):
            deps.append('ml_models')
        return deps

    def _get_branch_relationships(self) -> dict:
        """Find dependencies between branches"""
        relationships = {}
        for branch_name, branch_info in self.index['branches'].items():
            relationships[branch_name] = {
                'depends_on_data': branch_info.get('dependencies', []),
                'unique_files': branch_info.get('unique_files', []),
                'can_be_merged_to': self._get_merge_targets(branch_name)
            }
        return relationships

    def _get_merge_targets(self, branch: str) -> list:
        """Determine which branches this can merge to"""
        if branch == 'main':
            return []
        elif 'feature/' in branch:
            return ['develop', 'main']
        elif 'hotfix/' in branch:
            return ['main', 'develop']
        else:
            return ['main']

    def generate_index(self) -> dict:
        """Generate complete repository index"""
        print("Scanning repository branches...")

        branches = self.get_all_branches()
        main_branch = self._find_main_branch(branches)
        self.index['main_branch'] = main_branch

        print(f"Found {len(branches)} branches (main: {main_branch})")

        # Scan each branch
        for i, branch in enumerate(branches, 1):
            print(f"  {i}/{len(branches)} {branch}...", end=' ', flush=True)
            info = self.get_branch_info(branch)
            self.index['branches'][branch] = info
            print("✓")

        # Generate relationships
        self.index['dependencies'] = self._get_branch_relationships()

        # Build file index (which files are on which branches)
        self._build_file_index()

        # Create feature matrix
        self._build_feature_matrix()

        return self.index

    def _build_file_index(self):
        """Create reverse index: file -> branches"""
        file_index = defaultdict(list)
        for branch, info in self.index['branches'].items():
            unique = info.get('unique_files', [])
            for file in unique:
                file_index[file].append(branch)
        self.index['file_index'] = dict(file_index)

    def _build_feature_matrix(self):
        """Create matrix: features vs branches"""
        features = defaultdict(list)
        for branch, info in self.index['branches'].items():
            purpose = info.get('purpose', '')
            if 'Feature:' in purpose:
                feat = purpose.split('Feature:')[-1].strip()
                features[feat].append(branch)
        self.index['feature_matrix'] = dict(features)

    def _find_main_branch(self, branches: list) -> str:
        """Find main/master/trunk branch"""
        for name in ['main', 'master', 'trunk']:
            if name in branches:
                return name
        return branches[0] if branches else 'unknown'

    def save_json_index(self, output_file: str):
        """Save machine-readable index"""
        with open(output_file, 'w') as f:
            json.dump(self.index, f, indent=2, default=str)
        print(f"\n✓ JSON index saved: {output_file}")

    def save_markdown_index(self, output_file: str):
        """Save human-readable markdown index"""
        md = self._generate_markdown()
        with open(output_file, 'w') as f:
            f.write(md)
        print(f"✓ Markdown index saved: {output_file}")

    def _generate_markdown(self) -> str:
        """Generate markdown documentation"""
        md = []
        md.append("# Repository Index\n")
        md.append(f"**Generated:** {self.index['generated']}\n")
        md.append(f"**Main Branch:** `{self.index['main_branch']}`\n")
        md.append(f"**Total Branches:** {len(self.index['branches'])}\n\n")

        # Branch overview
        md.append("## Branch Overview\n")
        md.append("| Branch | Purpose | Files | Latest | Dependencies |\n")
        md.append("|--------|---------|-------|--------|---------------|\n")

        for branch, info in sorted(self.index['branches'].items()):
            if 'error' in info:
                continue
            purpose = info.get('purpose', 'Unknown')[:40]
            files = info.get('file_count', 0)
            commit = info.get('latest_commit', {}).get('message', '')[:30]
            deps = ', '.join(info.get('dependencies', [])[:2])
            md.append(f"| `{branch}` | {purpose} | {files} | {commit}... | {deps} |\n")

        # Features by branch
        md.append("\n## Features by Branch\n\n")
        for feature, branches in sorted(self.index['feature_matrix'].items()):
            md.append(f"### {feature}\n")
            for branch in branches:
                md.append(f"- `{branch}`\n")
            md.append("\n")

        # File uniqueness
        md.append("## Unique Files by Branch\n\n")
        for branch, info in sorted(self.index['branches'].items()):
            if 'error' in info:
                continue
            unique = info.get('unique_files', [])
            if unique:
                md.append(f"### {branch}\n")
                for f in unique[:5]:
                    md.append(f"- {f}\n")
                if len(unique) > 5:
                    md.append(f"... and {len(unique)-5} more\n")
                md.append("\n")

        # Dependencies
        md.append("## Data Dependencies\n\n")
        md.append("| Branch | Needs | Purpose |\n")
        md.append("|--------|-------|----------|\n")
        for branch, deps in self.index['dependencies'].items():
            branch_deps = deps.get('depends_on_data', [])
            if branch_deps:
                md.append(f"| `{branch}` | {', '.join(branch_deps)} | {self.index['branches'].get(branch, {}).get('purpose', '')} |\n")

        # Merge targets
        md.append("\n## Merge Strategy\n\n")
        for branch, deps in self.index['dependencies'].items():
            targets = deps.get('can_be_merged_to', [])
            if targets:
                md.append(f"- `{branch}` → {' → '.join(targets)}\n")

        return "".join(md)


def main():
    repo_path = '/Users/umashankar/Downloads/code/python_files'

    print("="*70)
    print("REPOSITORY INDEX GENERATOR")
    print("="*70)
    print()

    generator = RepositoryIndexGenerator(repo_path)
    index = generator.generate_index()

    # Save outputs
    generator.save_json_index('REPO_INDEX.json')
    generator.save_markdown_index('REPO_INDEX.md')

    print()
    print("="*70)
    print("INDEX SUMMARY")
    print("="*70)
    print(f"Main branch: {index['main_branch']}")
    print(f"Total branches: {len(index['branches'])}")
    print(f"Total unique files: {len(index['file_index'])}")
    print(f"Features tracked: {len(index['feature_matrix'])}")
    print()
    print("Files generated:")
    print("  • REPO_INDEX.json - Machine-readable index")
    print("  • REPO_INDEX.md - Human-readable markdown")
    print()
    print("Use REPO_INDEX.md to:")
    print("  ✓ See all branches and their purpose")
    print("  ✓ Find which files are unique to each branch")
    print("  ✓ Understand data dependencies")
    print("  ✓ Plan merges and integration")
    print("  ✓ Cross-reference between branches")


if __name__ == '__main__':
    main()
