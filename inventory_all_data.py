#!/usr/bin/env python3
"""
Comprehensive Data Inventory & Deduplication Analysis
Creates a complete list of all data, identifies duplicates, and proposes optimization
"""

import os
import json
import hashlib
import subprocess
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Tuple

class DataInventoryAnalyzer:
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path)
        self.files_by_hash = defaultdict(list)
        self.files_by_extension = defaultdict(list)
        self.total_size = 0
        self.duplicate_size = 0
        self.inventory = []

    def compute_hash(self, file_path: Path) -> str:
        """Compute SHA256 of file"""
        sha256 = hashlib.sha256()
        try:
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(65536), b''):
                    sha256.update(chunk)
            return sha256.hexdigest()
        except:
            return None

    def scan_repository(self) -> Dict:
        """Scan entire repository for all data files"""
        print("Scanning repository for all files...")

        file_count = 0
        for file_path in self.repo_path.rglob('*'):
            if not file_path.is_file():
                continue
            if '.git' in file_path.parts or '__pycache__' in file_path.parts:
                continue

            try:
                size = file_path.stat().st_size
                ext = file_path.suffix or 'no_extension'
                relative_path = file_path.relative_to(self.repo_path)

                # Compute hash for duplicates
                file_hash = self.compute_hash(file_path)

                if file_hash:
                    self.files_by_hash[file_hash].append({
                        'path': str(relative_path),
                        'size': size,
                        'type': ext
                    })

                self.files_by_extension[ext].append({
                    'path': str(relative_path),
                    'size': size,
                    'hash': file_hash
                })

                self.total_size += size
                file_count += 1

                if file_count % 100 == 0:
                    print(f"  Scanned {file_count} files...")

            except Exception as e:
                print(f"  Skipped {file_path}: {e}")

        print(f"✓ Scanned {file_count} files total")
        return self.generate_report()

    def generate_report(self) -> Dict:
        """Generate comprehensive data inventory report"""

        # Find duplicates
        duplicates = {}
        for file_hash, files in self.files_by_hash.items():
            if len(files) > 1:
                self.duplicate_size += sum(f['size'] for f in files[1:])
                duplicates[file_hash] = files

        # Organize by data type
        data_types = self._categorize_files()

        report = {
            'timestamp': datetime.now().isoformat(),
            'repository': str(self.repo_path),
            'summary': {
                'total_files': sum(len(files) for files in self.files_by_extension.values()),
                'total_size_mb': self.total_size / (1024**2),
                'duplicate_files': len(duplicates),
                'duplicate_size_mb': self.duplicate_size / (1024**2),
                'potential_savings_mb': self.duplicate_size / (1024**2)
            },
            'by_file_type': self._file_type_summary(),
            'duplicates': self._duplicate_summary(duplicates),
            'data_categories': data_types,
            'redundant_data': self._identify_redundant_data(),
            'deduplication_strategy': self._create_strategy()
        }

        return report

    def _categorize_files(self) -> Dict:
        """Categorize files by data type"""
        categories = {
            'cache_data': [],
            'parquet_files': [],
            'csv_data': [],
            'json_config': [],
            'code_files': [],
            'documentation': [],
            'binary_data': [],
            'other': []
        }

        for ext, files in self.files_by_extension.items():
            if 'parquet' in ext:
                categories['parquet_files'].extend(files)
            elif ext in ['.csv', '.tsv']:
                categories['csv_data'].extend(files)
            elif ext in ['.json', '.yaml', '.yml']:
                categories['json_config'].extend(files)
            elif ext in ['.py', '.js', '.sh', '.sql']:
                categories['code_files'].extend(files)
            elif ext in ['.md', '.txt', '.rst']:
                categories['documentation'].extend(files)
            elif ext in ['.db', '.sqlite', '.pkl', '.h5', '.npy']:
                categories['binary_data'].extend(files)
            else:
                categories['other'].extend(files)

        # Calculate sizes
        for category, files in categories.items():
            total = sum(f['size'] for f in files)
            categories[category] = {
                'count': len(files),
                'size_mb': total / (1024**2),
                'files': files[:5]  # Show top 5
            }

        return categories

    def _file_type_summary(self) -> Dict:
        """Summary of files by type"""
        summary = {}
        for ext, files in self.files_by_extension.items():
            total_size = sum(f['size'] for f in files)
            summary[ext] = {
                'count': len(files),
                'size_mb': total_size / (1024**2),
                'avg_size_kb': (total_size / len(files)) / 1024 if files else 0
            }
        return summary

    def _duplicate_summary(self, duplicates: Dict) -> List:
        """Summarize duplicate files"""
        dup_list = []
        for file_hash, files in duplicates.items():
            size_per_copy = files[0]['size']
            total_wasted = size_per_copy * (len(files) - 1)

            dup_list.append({
                'hash': file_hash[:8] + '...',
                'copies': len(files),
                'size_per_copy_mb': size_per_copy / (1024**2),
                'total_wasted_mb': total_wasted / (1024**2),
                'paths': [f['path'] for f in files],
                'recommendation': self._get_dup_recommendation(files)
            })

        # Sort by wasted space
        return sorted(dup_list, key=lambda x: x['total_wasted_mb'], reverse=True)

    def _get_dup_recommendation(self, files: List) -> str:
        """Get recommendation for duplicate files"""
        if 'cache_seed' in files[0]['path']:
            return 'Keep in cache_seed/, symlink from other branches'
        elif 'parquet' in files[0]['path']:
            return 'Move to Git LFS, symlink locally'
        else:
            return 'Keep original, delete/symlink others'

    def _identify_redundant_data(self) -> List:
        """Identify redundant data across branches"""
        redundant = []

        # Cache files
        cache_files = [f for ext in self.files_by_extension
                      for f in self.files_by_extension.get(ext, [])
                      if 'cache' in f['path']]

        # Parquet files
        parquet_files = self.files_by_extension.get('.parquet', [])

        # Screening results
        screening_files = [f for ext in self.files_by_extension
                          for f in self.files_by_extension.get(ext, [])
                          if 'screening_' in f['path']]

        if cache_files:
            redundant.append({
                'type': 'Cache Data',
                'count': len(cache_files),
                'size_mb': sum(f['size'] for f in cache_files) / (1024**2),
                'recommendation': 'Centralize in cache_seed/ directory, symlink from branches',
                'priority': 'HIGH'
            })

        if parquet_files:
            redundant.append({
                'type': 'Parquet Data',
                'count': len(parquet_files),
                'size_mb': sum(f['size'] for f in parquet_files) / (1024**2),
                'recommendation': 'Move to Git LFS, keep single copy, reference from branches',
                'priority': 'HIGH'
            })

        if screening_files:
            redundant.append({
                'type': 'Screening Results',
                'count': len(screening_files),
                'size_mb': sum(f['size'] for f in screening_files) / (1024**2),
                'recommendation': 'Archive to results/ directory, reference by timestamp',
                'priority': 'MEDIUM'
            })

        return redundant

    def _create_strategy(self) -> Dict:
        """Create deduplication strategy"""
        return {
            'approach': 'Single Source of Truth with Symlinks',
            'phases': [
                {
                    'phase': 1,
                    'name': 'Inventory & Analysis',
                    'status': 'COMPLETE',
                    'tasks': [
                        'Scan all branches for duplicate files',
                        'Identify data hotspots (cache, parquet)',
                        'Calculate redundancy percentages'
                    ]
                },
                {
                    'phase': 2,
                    'name': 'Centralization',
                    'status': 'READY',
                    'tasks': [
                        'Create shared_data/ directory for centralized data',
                        'Move cache_seed/ files to shared_data/cache_seed/',
                        'Move parquet files to shared_data/data/',
                        'Create .gitignore rules for shared data'
                    ]
                },
                {
                    'phase': 3,
                    'name': 'Symlink Setup',
                    'status': 'READY',
                    'tasks': [
                        'Create symlinks from branches to shared_data/',
                        'Update .gitignore to exclude symlink targets',
                        'Test symlinks work across all branches',
                        'Verify functionality with scripts'
                    ]
                },
                {
                    'phase': 4,
                    'name': 'Git LFS Migration',
                    'status': 'READY',
                    'tasks': [
                        'Configure Git LFS for .parquet files',
                        'Configure Git LFS for large CSV/JSON',
                        'Migrate existing large files to LFS',
                        'Update .gitattributes'
                    ]
                },
                {
                    'phase': 5,
                    'name': 'Documentation & Automation',
                    'status': 'READY',
                    'tasks': [
                        'Document shared data structure',
                        'Create setup script for new clones',
                        'Add validation to vCRUD workflow',
                        'Brief team on new structure'
                    ]
                }
            ],
            'expected_savings': {
                'redundant_data_mb': self.duplicate_size / (1024**2),
                'git_repo_reduction_percent': (self.duplicate_size / self.total_size * 100) if self.total_size > 0 else 0,
                'clone_time_reduction': '50-70% faster'
            }
        }

    def save_report(self, output_file: str):
        """Save inventory report to JSON"""
        report = self.generate_report()

        with open(output_file, 'w') as f:
            json.dump(report, f, indent=2, default=str)

        print(f"\n✓ Report saved: {output_file}")

        # Print summary
        print("\n" + "="*70)
        print("DATA INVENTORY SUMMARY")
        print("="*70)
        print(f"Total Files: {report['summary']['total_files']}")
        print(f"Total Size: {report['summary']['total_size_mb']:.1f} MB")
        print(f"Duplicate Files: {report['summary']['duplicate_files']}")
        print(f"Wasted Space: {report['summary']['duplicate_size_mb']:.1f} MB")
        print(f"Potential Savings: {report['summary']['potential_savings_mb']:.1f} MB")
        print("="*70)

        return report


def main():
    repo_path = '/Users/umashankar/Downloads/code/python_files'

    print("="*70)
    print("COMPREHENSIVE DATA INVENTORY & DEDUPLICATION ANALYSIS")
    print("="*70)
    print()

    analyzer = DataInventoryAnalyzer(repo_path)
    report = analyzer.scan_repository()

    # Save report
    output_file = 'data_inventory_report.json'
    analyzer.save_report(output_file)

    # Print top duplicates
    print("\nTOP DUPLICATES (by wasted space):")
    print("-"*70)
    for i, dup in enumerate(report['duplicates'][:10], 1):
        print(f"\n{i}. Wasted: {dup['total_wasted_mb']:.1f} MB ({dup['copies']}x copies)")
        print(f"   Files:")
        for path in dup['paths'][:3]:
            print(f"     - {path}")
        if len(dup['paths']) > 3:
            print(f"     ... and {len(dup['paths']) - 3} more")
        print(f"   Action: {dup['recommendation']}")

    print("\n" + "="*70)
    print("NEXT STEPS:")
    print("="*70)
    print("1. Review data_inventory_report.json")
    print("2. Run: python create_shared_data_structure.py")
    print("3. Run: python setup_symlinks.py")
    print("4. Configure Git LFS")
    print("5. Run: vCRUD validation")


if __name__ == '__main__':
    main()
