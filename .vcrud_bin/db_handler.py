"""
PostgreSQL Database Handler for GitHub/Local File Tracking with Compression
Supports Git-backed versioning, compression optimization, and cross-branch analysis
"""

import json
import gzip
import hashlib
import subprocess
from pathlib import Path
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, Dict, List, Tuple
import psycopg2
from psycopg2.extras import RealDictCursor, Json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class FileRecord:
    """Represents a file with metadata and compression info"""
    path: str
    size_bytes: int
    checksum: str
    branch: str
    last_modified: datetime
    git_commit: Optional[str] = None
    compressed_size: Optional[int] = None
    compression_ratio: Optional[float] = None
    retrieval_count: int = 0


class DatabaseHandler:
    """PostgreSQL handler for branch file tracking and vCRUD operations"""

    def __init__(self, db_url: str):
        """
        Initialize database connection
        db_url: postgresql://user:password@host:port/database
        """
        self.db_url = db_url
        self.conn = None
        self.connect()
        self._init_schema()

    def connect(self):
        """Establish PostgreSQL connection"""
        try:
            self.conn = psycopg2.connect(self.db_url)
            logger.info("✓ Connected to PostgreSQL")
        except Exception as e:
            logger.error(f"✗ Connection failed: {e}")
            raise

    def _init_schema(self):
        """Create tables if they don't exist"""
        with self.conn.cursor() as cur:
            # Branches table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS branches (
                    id SERIAL PRIMARY KEY,
                    branch_name VARCHAR(255) UNIQUE NOT NULL,
                    last_scanned TIMESTAMP,
                    total_files INT DEFAULT 0,
                    total_size_bytes BIGINT DEFAULT 0,
                    total_compressed_bytes BIGINT DEFAULT 0,
                    metadata JSONB DEFAULT '{}'
                )
            """)

            # Files table (git-backed versioning)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    id SERIAL PRIMARY KEY,
                    file_path VARCHAR(1024) NOT NULL,
                    branch_id INT NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
                    size_bytes BIGINT NOT NULL,
                    checksum VARCHAR(64) NOT NULL,
                    git_commit VARCHAR(40),
                    compressed_size BIGINT,
                    compression_ratio FLOAT,
                    last_modified TIMESTAMP NOT NULL,
                    retrieval_count INT DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(file_path, branch_id, git_commit)
                )
            """)

            # File version history (leverages Git commits)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS file_versions (
                    id SERIAL PRIMARY KEY,
                    file_id INT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                    git_commit VARCHAR(40) NOT NULL,
                    size_bytes BIGINT,
                    compressed_size BIGINT,
                    changed_at TIMESTAMP,
                    operation VARCHAR(10) DEFAULT 'UPDATE'
                )
            """)

            # Cross-branch file deduplication
            cur.execute("""
                CREATE TABLE IF NOT EXISTS file_dedup (
                    id SERIAL PRIMARY KEY,
                    checksum VARCHAR(64) UNIQUE NOT NULL,
                    primary_file_id INT REFERENCES files(id),
                    duplicate_count INT DEFAULT 1,
                    total_wasted_bytes BIGINT DEFAULT 0,
                    branches_list JSONB DEFAULT '[]'
                )
            """)

            # Retrieval optimization cache
            cur.execute("""
                CREATE TABLE IF NOT EXISTS retrieval_cache (
                    id SERIAL PRIMARY KEY,
                    file_id INT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                    branch_id INT NOT NULL REFERENCES branches(id),
                    access_count INT DEFAULT 0,
                    last_accessed TIMESTAMP,
                    cache_priority INT DEFAULT 0
                )
            """)

            # Compression stats for optimization
            cur.execute("""
                CREATE TABLE IF NOT EXISTS compression_stats (
                    id SERIAL PRIMARY KEY,
                    branch_id INT NOT NULL REFERENCES branches(id),
                    total_original_bytes BIGINT,
                    total_compressed_bytes BIGINT,
                    overall_ratio FLOAT,
                    best_ratio FLOAT,
                    worst_ratio FLOAT,
                    computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Indexes for performance
            cur.execute("CREATE INDEX IF NOT EXISTS idx_files_branch ON files(branch_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_files_checksum ON files(checksum)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_versions_commit ON file_versions(git_commit)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_dedup_checksum ON file_dedup(checksum)")

            self.conn.commit()
            logger.info("✓ Schema initialized")

    def create_branch(self, branch_name: str) -> int:
        """Create or get branch_id"""
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO branches (branch_name) VALUES (%s) ON CONFLICT (branch_name) DO UPDATE SET branch_name=EXCLUDED.branch_name RETURNING id",
                (branch_name,)
            )
            branch_id = cur.fetchone()[0]
            self.conn.commit()
            return branch_id

    def create_file(self, record: FileRecord) -> int:
        """CREATE: Add file to database"""
        branch_id = self.create_branch(record.branch)

        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO files
                (file_path, branch_id, size_bytes, checksum, git_commit,
                 compressed_size, compression_ratio, last_modified)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (file_path, branch_id, git_commit) DO UPDATE SET
                    size_bytes=EXCLUDED.size_bytes,
                    updated_at=CURRENT_TIMESTAMP
                RETURNING id
            """, (
                record.path, branch_id, record.size_bytes, record.checksum,
                record.git_commit, record.compressed_size, record.compression_ratio,
                record.last_modified
            ))
            file_id = cur.fetchone()[0]

            # Track version in git history
            if record.git_commit:
                cur.execute("""
                    INSERT INTO file_versions
                    (file_id, git_commit, size_bytes, compressed_size, changed_at, operation)
                    VALUES (%s, %s, %s, %s, %s, 'CREATE')
                """, (file_id, record.git_commit, record.size_bytes, record.compressed_size, datetime.now()))

            self.conn.commit()
            logger.info(f"✓ Created file: {record.path} on {record.branch}")
            return file_id

    def read_file(self, file_path: str, branch: str) -> Optional[FileRecord]:
        """READ: Retrieve file metadata"""
        branch_id = self.create_branch(branch)

        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM files
                WHERE file_path=%s AND branch_id=%s
                ORDER BY created_at DESC LIMIT 1
            """, (file_path, branch_id))
            row = cur.fetchone()

            if row:
                # Update retrieval count
                cur.execute(
                    "UPDATE files SET retrieval_count=retrieval_count+1 WHERE id=%s",
                    (row['id'],)
                )
                self.conn.commit()

                return FileRecord(**row)
        return None

    def update_file(self, record: FileRecord) -> bool:
        """UPDATE: Modify file metadata (Git-backed)"""
        branch_id = self.create_branch(record.branch)

        with self.conn.cursor() as cur:
            cur.execute("""
                UPDATE files SET
                    size_bytes=%s,
                    checksum=%s,
                    compressed_size=%s,
                    compression_ratio=%s,
                    last_modified=%s,
                    updated_at=CURRENT_TIMESTAMP
                WHERE file_path=%s AND branch_id=%s
                RETURNING id
            """, (
                record.size_bytes, record.checksum, record.compressed_size,
                record.compression_ratio, record.last_modified, record.path, branch_id
            ))

            file_id = cur.fetchone()
            if file_id:
                # Log version change
                if record.git_commit:
                    cur.execute("""
                        INSERT INTO file_versions
                        (file_id, git_commit, size_bytes, compressed_size, changed_at, operation)
                        VALUES (%s, %s, %s, %s, %s, 'UPDATE')
                    """, (file_id[0], record.git_commit, record.size_bytes, record.compressed_size, datetime.now()))
                self.conn.commit()
                logger.info(f"✓ Updated file: {record.path}")
                return True
        return False

    def delete_file(self, file_path: str, branch: str, git_commit: Optional[str] = None) -> bool:
        """DELETE: Remove file (soft delete with version history)"""
        branch_id = self.create_branch(branch)

        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM files WHERE file_path=%s AND branch_id=%s",
                (file_path, branch_id)
            )
            file_id = cur.fetchone()

            if file_id:
                # Log deletion in version history
                if git_commit:
                    cur.execute("""
                        INSERT INTO file_versions
                        (file_id, git_commit, changed_at, operation)
                        VALUES (%s, %s, %s, 'DELETE')
                    """, (file_id[0], git_commit, datetime.now()))

                # Mark as deleted (soft delete)
                cur.execute(
                    "DELETE FROM files WHERE id=%s",
                    (file_id[0],)
                )
                self.conn.commit()
                logger.info(f"✓ Deleted file: {file_path} from {branch}")
                return True
        return False

    def list_files_by_branch(self, branch: str) -> List[FileRecord]:
        """List all files on a branch"""
        branch_id = self.create_branch(branch)

        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM files WHERE branch_id=%s ORDER BY file_path",
                (branch_id,)
            )
            return [FileRecord(**row) for row in cur.fetchall()]

    def get_branch_stats(self, branch: str) -> Dict:
        """Get compression and size stats for a branch"""
        branch_id = self.create_branch(branch)

        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    COUNT(*) as total_files,
                    SUM(size_bytes) as total_size,
                    SUM(compressed_size) as total_compressed,
                    AVG(compression_ratio) as avg_ratio,
                    MAX(compression_ratio) as best_ratio,
                    MIN(compression_ratio) as worst_ratio,
                    SUM(size_bytes) - COALESCE(SUM(compressed_size), 0) as bytes_saved
                FROM files WHERE branch_id=%s
            """, (branch_id,))

            stats = cur.fetchone()
            return {
                'branch': branch,
                'total_files': stats['total_files'] or 0,
                'total_size_mb': (stats['total_size'] or 0) / (1024**2),
                'compressed_size_mb': (stats['total_compressed'] or 0) / (1024**2),
                'avg_compression_ratio': stats['avg_ratio'] or 0,
                'best_ratio': stats['best_ratio'] or 0,
                'worst_ratio': stats['worst_ratio'] or 0,
                'bytes_saved_mb': (stats['bytes_saved'] or 0) / (1024**2)
            }

    def find_duplicates(self) -> List[Dict]:
        """Find identical files across branches (deduplication)"""
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    checksum,
                    COUNT(*) as duplicate_count,
                    SUM(size_bytes) as total_wasted,
                    ARRAY_AGG(DISTINCT file_path) as file_paths,
                    ARRAY_AGG(DISTINCT b.branch_name) as branches
                FROM files f
                JOIN branches b ON f.branch_id = b.id
                GROUP BY checksum
                HAVING COUNT(*) > 1
                ORDER BY total_wasted DESC
            """)
            return cur.fetchall()

    def get_file_history(self, file_path: str, branch: str) -> List[Dict]:
        """Get Git-backed version history for a file"""
        branch_id = self.create_branch(branch)

        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT fv.*, f.file_path
                FROM file_versions fv
                JOIN files f ON fv.file_id = f.id
                WHERE f.file_path=%s AND f.branch_id=%s
                ORDER BY fv.changed_at DESC
            """, (file_path, branch_id))
            return cur.fetchall()

    def get_top_retrieval_files(self, branch: str, limit: int = 10) -> List[Dict]:
        """Get most frequently accessed files (optimization hint)"""
        branch_id = self.create_branch(branch)

        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT file_path, retrieval_count, size_bytes, compressed_size
                FROM files
                WHERE branch_id=%s
                ORDER BY retrieval_count DESC
                LIMIT %s
            """, (branch_id, limit))
            return cur.fetchall()

    def compare_branches(self, branch1: str, branch2: str) -> Dict:
        """Compare files between two branches"""
        b1_id = self.create_branch(branch1)
        b2_id = self.create_branch(branch2)

        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Files only in branch1
            cur.execute("""
                SELECT file_path FROM files WHERE branch_id=%s
                EXCEPT
                SELECT file_path FROM files WHERE branch_id=%s
                ORDER BY file_path
            """, (b1_id, b2_id))
            only_in_b1 = [row['file_path'] for row in cur.fetchall()]

            # Files only in branch2
            cur.execute("""
                SELECT file_path FROM files WHERE branch_id=%s
                EXCEPT
                SELECT file_path FROM files WHERE branch_id=%s
                ORDER BY file_path
            """, (b2_id, b1_id))
            only_in_b2 = [row['file_path'] for row in cur.fetchall()]

            # Files in both
            cur.execute("""
                SELECT COUNT(*) as count FROM files WHERE branch_id=%s
                AND file_path IN (SELECT file_path FROM files WHERE branch_id=%s)
            """, (b1_id, b2_id))
            common = cur.fetchone()['count']

        return {
            'branch1': branch1,
            'branch2': branch2,
            'only_in_branch1': only_in_b1,
            'only_in_branch2': only_in_b2,
            'common_files': common
        }

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            logger.info("✓ Database connection closed")


class CompressionManager:
    """Handle compression/decompression with optimization tracking"""

    @staticmethod
    def compress_file(file_path: str) -> Tuple[bytes, float]:
        """
        Compress file using gzip, return compressed data and ratio
        Returns: (compressed_bytes, compression_ratio)
        """
        original_data = Path(file_path).read_bytes()
        compressed = gzip.compress(original_data, compresslevel=6)
        ratio = len(compressed) / len(original_data) if original_data else 0
        return compressed, ratio

    @staticmethod
    def decompress_file(compressed_data: bytes) -> bytes:
        """Decompress gzip data"""
        return gzip.decompress(compressed_data)

    @staticmethod
    def compute_checksum(file_path: str) -> str:
        """Compute SHA256 checksum of file"""
        sha256 = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                sha256.update(chunk)
        return sha256.hexdigest()


class GitBranchScanner:
    """Scan Git branches for files and metadata"""

    def __init__(self, repo_path: str):
        self.repo_path = repo_path

    def get_all_branches(self) -> List[str]:
        """Get list of all Git branches"""
        result = subprocess.run(
            ['git', '-C', self.repo_path, 'branch', '-a'],
            capture_output=True, text=True
        )
        branches = [b.strip().lstrip('* ') for b in result.stdout.split('\n') if b.strip()]
        return branches

    def get_branch_files(self, branch: str) -> Dict[str, Dict]:
        """Get all files on a branch with metadata"""
        try:
            # Switch to branch
            subprocess.run(['git', '-C', self.repo_path, 'checkout', branch],
                          capture_output=True, check=True)

            files = {}
            repo_obj = Path(self.repo_path)

            for file_path in repo_obj.rglob('*'):
                if file_path.is_file() and '.git' not in file_path.parts:
                    relative_path = file_path.relative_to(repo_obj)

                    # Get file metadata
                    size = file_path.stat().st_size
                    checksum = CompressionManager.compute_checksum(str(file_path))

                    # Get last commit for this file
                    result = subprocess.run(
                        ['git', '-C', self.repo_path, 'log', '-1', '--format=%H', '--', str(relative_path)],
                        capture_output=True, text=True
                    )
                    git_commit = result.stdout.strip() or None

                    files[str(relative_path)] = {
                        'size': size,
                        'checksum': checksum,
                        'git_commit': git_commit,
                        'path_obj': file_path
                    }

            return files
        except subprocess.CalledProcessError as e:
            logger.error(f"✗ Git error on {branch}: {e}")
            return {}

    def get_commit_history(self, branch: str, file_path: str) -> List[str]:
        """Get all commits that touched a file on a branch"""
        result = subprocess.run(
            ['git', '-C', self.repo_path, 'log', '--oneline', branch, '--', file_path],
            capture_output=True, text=True
        )
        return result.stdout.strip().split('\n') if result.stdout.strip() else []
