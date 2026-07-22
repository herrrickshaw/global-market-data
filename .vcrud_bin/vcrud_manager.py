"""
Versioned CRUD Manager - Unified interface for Git-backed file tracking
Handles Create, Read, Update, Delete operations with full version history
"""

from db_handler import DatabaseHandler, FileRecord, CompressionManager, GitBranchScanner
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LocalFileIndexer:
    """Index local file system and compute metadata"""

    def __init__(self, base_path: str):
        self.base_path = Path(base_path)

    def scan_directory(self, branch: str, patterns: Optional[List[str]] = None) -> List[FileRecord]:
        """
        Scan directory and create FileRecords
        patterns: list of glob patterns to include (default: all non-.git files)
        """
        if patterns is None:
            patterns = ['**/*']

        records = []
        for pattern in patterns:
            for file_path in self.base_path.glob(pattern):
                if file_path.is_file() and '.git' not in file_path.parts:
                    try:
                        size = file_path.stat().st_size
                        checksum = CompressionManager.compute_checksum(str(file_path))
                        relative_path = file_path.relative_to(self.base_path)

                        # Attempt compression ratio
                        compressed_data, ratio = CompressionManager.compress_file(str(file_path))
                        compressed_size = len(compressed_data)

                        record = FileRecord(
                            path=str(relative_path),
                            size_bytes=size,
                            checksum=checksum,
                            branch=branch,
                            last_modified=datetime.fromtimestamp(file_path.stat().st_mtime),
                            compressed_size=compressed_size,
                            compression_ratio=ratio
                        )
                        records.append(record)
                    except Exception as e:
                        logger.warning(f"✗ Skipped {file_path}: {e}")
                        continue

        logger.info(f"✓ Scanned {len(records)} files from {self.base_path}")
        return records


class GitHubRemoteSync:
    """Sync with GitHub repository (optional - for remote tracking)"""

    def __init__(self, repo_url: str, token: Optional[str] = None):
        self.repo_url = repo_url
        self.token = token

    def get_remote_branches(self) -> List[str]:
        """Get list of branches from remote GitHub repo"""
        # In production, use PyGithub or github3.py
        # For now, return placeholder
        logger.info("Remote sync would fetch branches from GitHub")
        return []

    def get_remote_file_size(self, branch: str, file_path: str) -> Optional[int]:
        """Get file size from remote without downloading"""
        # Use GitHub API to get file metadata
        logger.info(f"Remote sync would check size of {file_path} on {branch}")
        return None


class VersionedCRUDManager:
    """
    Unified vCRUD interface
    - C: Create files with automatic compression
    - R: Read files with retrieval optimization hints
    - U: Update files with Git commit tracking
    - D: Delete files with soft-delete version tracking
    """

    def __init__(self, db_handler: DatabaseHandler, repo_path: str):
        self.db = db_handler
        self.repo_path = Path(repo_path)
        self.scanner = GitBranchScanner(str(repo_path))
        self.compression = CompressionManager()

    # ========== CREATE ==========
    def create_file(self, file_path: str, branch: str, git_commit: Optional[str] = None) -> int:
        """
        CREATE: Add new file to tracking
        Automatically compresses and indexes
        """
        full_path = self.repo_path / file_path
        if not full_path.exists():
            logger.error(f"✗ File not found: {full_path}")
            return -1

        size = full_path.stat().st_size
        checksum = self.compression.compute_checksum(str(full_path))

        # Compress for storage optimization
        compressed_data, ratio = self.compression.compress_file(str(full_path))
        compressed_size = len(compressed_data)

        record = FileRecord(
            path=file_path,
            size_bytes=size,
            checksum=checksum,
            branch=branch,
            last_modified=datetime.fromtimestamp(full_path.stat().st_mtime),
            git_commit=git_commit,
            compressed_size=compressed_size,
            compression_ratio=ratio
        )

        file_id = self.db.create_file(record)
        logger.info(f"✓ Created: {file_path} ({size:,} bytes → {compressed_size:,} bytes, ratio: {ratio:.2%})")
        return file_id

    def create_many(self, branch: str, file_paths: List[str], git_commit: Optional[str] = None):
        """Batch CREATE multiple files"""
        results = []
        for file_path in file_paths:
            try:
                file_id = self.create_file(file_path, branch, git_commit)
                results.append((file_path, file_id, "✓"))
            except Exception as e:
                logger.error(f"✗ Failed to create {file_path}: {e}")
                results.append((file_path, -1, str(e)))

        logger.info(f"✓ Batch CREATE: {len([r for r in results if r[2] == '✓'])}/{len(results)} successful")
        return results

    # ========== READ ==========
    def read_file(self, file_path: str, branch: str) -> Optional[FileRecord]:
        """
        READ: Retrieve file metadata and track access
        Increments retrieval counter for optimization
        """
        record = self.db.read_file(file_path, branch)
        if record:
            logger.info(f"✓ Read: {file_path} (accessed {record.retrieval_count} times)")
            return record
        logger.warning(f"✗ File not found: {file_path} on {branch}")
        return None

    def read_with_retrieval_hints(self, branch: str) -> Dict:
        """
        Read all files on branch with optimization hints
        Returns top files to cache locally for faster access
        """
        stats = self.db.get_branch_stats(branch)
        top_files = self.db.get_top_retrieval_files(branch, limit=10)

        return {
            'branch': branch,
            'stats': stats,
            'top_retrieval_candidates': top_files,
            'recommendation': self._get_retrieval_recommendation(top_files, stats)
        }

    def _get_retrieval_recommendation(self, top_files: List[Dict], stats: Dict) -> str:
        """Generate optimization recommendation"""
        if not top_files:
            return "No retrieval history yet. Start tracking file accesses."

        top_file = top_files[0]
        access_pct = (top_file['retrieval_count'] / sum(f['retrieval_count'] for f in top_files)) * 100
        size_mb = top_file['size_bytes'] / (1024**2)

        return (
            f"Pre-cache '{top_file['file_path']}' ({size_mb:.1f} MB, "
            f"{access_pct:.1f}% of traffic). "
            f"Total {stats['total_files']} files, {stats['total_size_mb']:.1f} MB, "
            f"{stats['avg_compression_ratio']:.1%} avg compression."
        )

    # ========== UPDATE ==========
    def update_file(self, file_path: str, branch: str, git_commit: Optional[str] = None) -> bool:
        """
        UPDATE: Recompute and update file metadata after local changes
        Creates version entry tracking the update
        """
        full_path = self.repo_path / file_path
        if not full_path.exists():
            logger.error(f"✗ File not found: {full_path}")
            return False

        size = full_path.stat().st_size
        checksum = self.compression.compute_checksum(str(full_path))
        compressed_data, ratio = self.compression.compress_file(str(full_path))
        compressed_size = len(compressed_data)

        record = FileRecord(
            path=file_path,
            size_bytes=size,
            checksum=checksum,
            branch=branch,
            last_modified=datetime.fromtimestamp(full_path.stat().st_mtime),
            git_commit=git_commit,
            compressed_size=compressed_size,
            compression_ratio=ratio
        )

        success = self.db.update_file(record)
        if success:
            logger.info(f"✓ Updated: {file_path} (now {size:,} bytes, ratio: {ratio:.2%})")
        return success

    # ========== DELETE ==========
    def delete_file(self, file_path: str, branch: str, git_commit: Optional[str] = None) -> bool:
        """
        DELETE: Soft delete with version tracking
        Preserves history via Git commit references
        """
        success = self.db.delete_file(file_path, branch, git_commit)
        if success:
            logger.info(f"✓ Deleted: {file_path} from {branch} (soft delete, history preserved)")
        return success

    # ========== ANALYSIS & OPTIMIZATION ==========
    def scan_branch_and_index(self, branch: str) -> Dict:
        """
        Full scan of a Git branch and populate database
        Returns summary of indexing results
        """
        logger.info(f"Starting branch scan: {branch}")
        files = self.scanner.get_branch_files(branch)

        results = []
        for file_path, metadata in files.items():
            try:
                record = FileRecord(
                    path=file_path,
                    size_bytes=metadata['size'],
                    checksum=metadata['checksum'],
                    branch=branch,
                    last_modified=datetime.now(),
                    git_commit=metadata['git_commit']
                )

                # Compute compression
                full_path = metadata['path_obj']
                if full_path.stat().st_size < 100 * 1024 * 1024:  # Only compress files < 100MB
                    compressed_data, ratio = self.compression.compress_file(str(full_path))
                    record.compressed_size = len(compressed_data)
                    record.compression_ratio = ratio

                file_id = self.db.create_file(record)
                results.append({'file': file_path, 'id': file_id, 'size': metadata['size']})
            except Exception as e:
                logger.warning(f"Skipped {file_path}: {e}")

        logger.info(f"✓ Indexed {len(results)} files on {branch}")
        stats = self.db.get_branch_stats(branch)
        return {
            'branch': branch,
            'files_indexed': len(results),
            'stats': stats
        }

    def find_duplicates_across_branches(self) -> List[Dict]:
        """Find and report identical files across branches"""
        duplicates = self.db.find_duplicates()

        logger.info(f"Found {len(duplicates)} unique files with duplicates")
        for dup in duplicates:
            wasted = dup['total_wasted'] / (1024**2)
            logger.info(
                f"  Checksum {dup['checksum'][:8]}...: "
                f"{dup['duplicate_count']}x, {wasted:.1f} MB wasted, "
                f"branches: {', '.join(dup['branches'])}"
            )

        return duplicates

    def compare_branches(self, branch1: str, branch2: str) -> Dict:
        """Compare file sets between two branches"""
        comparison = self.db.compare_branches(branch1, branch2)
        logger.info(f"Branch comparison {branch1} vs {branch2}:")
        logger.info(f"  Only in {branch1}: {len(comparison['only_in_branch1'])} files")
        logger.info(f"  Only in {branch2}: {len(comparison['only_in_branch2'])} files")
        logger.info(f"  Common: {comparison['common_files']} files")
        return comparison

    def get_file_history(self, file_path: str, branch: str) -> List[Dict]:
        """Get full version history of a file (Git-backed)"""
        history = self.db.get_file_history(file_path, branch)
        logger.info(f"File history for {file_path} on {branch}: {len(history)} versions")
        return history

    def export_summary(self, branch: str) -> Dict:
        """Export complete summary for a branch"""
        files = self.db.list_files_by_branch(branch)
        stats = self.db.get_branch_stats(branch)
        top_files = self.db.get_top_retrieval_files(branch, limit=20)

        return {
            'branch': branch,
            'timestamp': datetime.now().isoformat(),
            'statistics': stats,
            'total_files': len(files),
            'top_accessed_files': top_files,
            'all_files': [
                {
                    'path': f.path,
                    'size_mb': f.size_bytes / (1024**2),
                    'compression_ratio': f.compression_ratio,
                    'checksum': f.checksum,
                    'retrieval_count': f.retrieval_count
                }
                for f in files
            ]
        }

    def optimize_storage(self, branch: str, min_compression_ratio: float = 0.85) -> Dict:
        """
        Identify optimization opportunities
        Flags files with poor compression for review
        """
        files = self.db.list_files_by_branch(branch)

        poor_compression = [
            f for f in files
            if f.compression_ratio and f.compression_ratio > min_compression_ratio
        ]

        duplicates = self.db.find_duplicates()
        total_duplicate_waste = sum(d['total_wasted'] or 0 for d in duplicates)

        return {
            'branch': branch,
            'poor_compression_candidates': [
                {
                    'path': f.path,
                    'size_mb': f.size_bytes / (1024**2),
                    'ratio': f.compression_ratio,
                    'note': 'Consider alternative compression or storage'
                }
                for f in poor_compression[:10]
            ],
            'total_duplicate_waste_mb': total_duplicate_waste / (1024**2),
            'optimization_potential': {
                'poor_compression_files': len(poor_compression),
                'total_duplicate_files': len(duplicates),
                'estimated_savings_mb': (
                    (sum(f.size_bytes for f in poor_compression) / (1024**2)) +
                    (total_duplicate_waste / (1024**2))
                )
            }
        }
