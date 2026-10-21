"""Frozen local files. Evaluation code has no database connection or business reader."""
import csv
import hashlib
import json
from pathlib import Path

DATASET = "rec-course-v3-train5-1.0.0"
SPLIT = "temporal-v1-cold500-train5-v3"
SCHEMA = "student-eight-v3"
MANIFEST_SHA = "fcfb9a195a56709773696aab02f769c691778af0eb433a1e0778a71c4b289b9e"
PACKAGE = Path(__file__).resolve().parents[1] / "data/packages" / DATASET
FEATURES = {"movies.csv", "training-catalog.csv", "history_users.csv"}
EVALUATION = {"evaluation/" + name for name in ["train.csv", "validation.csv", "test.csv",
    "evaluation-eligibility.csv", "per-user-counts.csv"]} | {
    f"evaluation/cold-k{k}-{part}.csv" for k in [0, 1, 5, 10] for part in ["support", "query", "users"]}


class ContractError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def require(condition, message: str, code: str = "PROVIDER_OUTPUT_INVALID"):
    if not condition:
        raise ContractError(code, message)


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


class FrozenData:
    def __init__(self, package: Path = PACKAGE):
        self.package = Path(package).resolve()
        path = self.package / "manifest.json"
        require(path.is_file() and sha256(path) == MANIFEST_SHA, "正式manifest摘要不符", "DATA_IDENTITY_INVALID")
        self.manifest = json.loads(path.read_text())
        require(tuple(self.manifest[k] for k in ["dataset_version", "split_version", "schema_version"]) ==
                (DATASET, SPLIT, SCHEMA), "固定数据版本不符", "DATA_IDENTITY_INVALID")

    def checked(self, name: str, *, evaluation: bool = False) -> Path:
        allowed = FEATURES | (EVALUATION if evaluation else set())
        require(name in allowed, "此入口不允许读取该文件（尤其business完整评分或cold-history）", "DATA_MODE_INVALID")
        path = (self.package / name).resolve()
        require(path.is_relative_to(self.package) and path.is_file(), "冻结文件不存在或越界", "DATA_IDENTITY_INVALID")
        expected = self.manifest["files"][name]
        require(path.stat().st_size == expected["bytes"] and sha256(path) == expected["sha256"],
                "冻结文件摘要不符：" + name, "DATA_IDENTITY_INVALID")
        return path

    def rows(self, name: str, *, evaluation: bool = False):
        path = self.checked(name, evaluation=evaluation)
        expected = self.manifest["files"][name]
        count = 0
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            require(reader.fieldnames == expected["columns"], "CSV字段不符：" + name, "DATA_IDENTITY_INVALID")
            for row in reader:
                require(None not in row and all(v is not None for v in row.values()), "CSV行字段不完整", "DATA_IDENTITY_INVALID")
                count += 1
                yield row
        require(count == expected["records"], "CSV行数不符：" + name, "DATA_IDENTITY_INVALID")

    def catalog(self) -> set[str]:
        ids = [row["movie_id"] for row in self.rows("training-catalog.csv")]
        require(len(ids) == len(set(ids)) == 8459, "电影目录数量或唯一性不符", "DATA_IDENTITY_INVALID")
        return set(ids)

    def training(self):
        """Only this file is available from the evaluation fitting入口."""
        return self.rows("evaluation/train.csv", evaluation=True)
