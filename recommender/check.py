"""python -m recommender.check: student self-check and evaluation contracts, never metrics."""
import argparse
import importlib
import json
from pathlib import Path
import sys

from pydantic import ValidationError

from .contracts import Algorithm, AlgorithmNotImplemented, PROVIDERS, RecommendationRequest, RecommendationResult, recommend
from .data import DATASET, SPLIT, PACKAGE, ContractError, FrozenData
from .evaluation import EvaluationResult, check_evaluation_result, check_predictions, inspect_inputs, task
from .validation import ModelManifest, check_model, check_result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    selfcheck = sub.add_parser("selfcheck")
    selfcheck.add_argument("--module", help="import an existing student module; no module is generated")
    selfcheck.add_argument("--algorithm", choices=[a.value for a in Algorithm])
    selfcheck.add_argument("--subject-kind", choices=["ml", "app"], default="ml")
    selfcheck.add_argument("--subject-id", type=int)
    selfcheck.add_argument("--mode", choices=["business", "evaluation"], default="business")
    selfcheck.add_argument("--limit", type=int, default=10)
    selfcheck.add_argument("--http-url", help="optional running API origin for real public ml-user 501 verification")
    inputs = sub.add_parser("evaluation-inputs")
    inputs.add_argument("--package", type=Path, default=PACKAGE)
    task_cmd = sub.add_parser("task")
    task_cmd.add_argument("--partition", choices=["validation", "test", "cold-k0", "cold-k1", "cold-k5", "cold-k10"], required=True)
    sub.add_parser("schemas")
    model = sub.add_parser("model")
    model.add_argument("path", type=Path); model.add_argument("--mode", choices=["business", "evaluation"], required=True)
    for name in ["predictions", "evaluation-result"]:
        check = sub.add_parser(name)
        check.add_argument("path", type=Path); check.add_argument("--model", type=Path, required=True)
        if name == "predictions": check.add_argument("--partition", choices=["validation", "test", "cold-k0", "cold-k1", "cold-k5", "cold-k10"], required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "schemas":
            result = {"model_manifest": ModelManifest.model_json_schema(), "evaluation_result": EvaluationResult.model_json_schema(),
                      "recommendation_result": RecommendationResult.model_json_schema()}
        elif args.command == "evaluation-inputs": result = inspect_inputs(FrozenData(args.package))
        elif args.command == "task": result = task(FrozenData(), args.partition)
        elif args.command == "model":
            raw = ModelManifest.model_validate_json(args.path.read_text())
            value = check_model(args.path, args.mode, raw.algorithm)
            result = {"status": "validated_model_binding_only", "model_version": value.model_version, "runtime_mode": value.runtime_mode}
        elif args.command == "predictions": result = check_predictions(args.path, args.model, args.partition, FrozenData())
        elif args.command == "evaluation-result":
            checked = check_evaluation_result(args.path, args.model, FrozenData())
            result = {"status": "validated_result_format_only", "declared_status": checked.status, "metrics_computed": False}
        else:
            if args.module:
                try: importlib.import_module(args.module)
                except Exception as exc: raise ContractError("PROVIDER_IMPORT_FAILED", "学生模块导入失败：" + type(exc).__name__) from None
            data = FrozenData()
            if args.subject_kind == "app" and args.subject_id is None:
                raise ContractError("REQUEST_CONTEXT_INVALID", "app自检必须显式指定真实account_id，不能回退到ml用户ID")
            if args.http_url and (args.subject_kind != "ml" or args.mode != "business"):
                raise ContractError("HTTP_TARGET_INVALID", "公开HTTP自检仅用于business模式的ml历史用户")
            uid = args.subject_id if args.subject_id is not None else int(next(data.rows("history_users.csv"))["ml_user_id"])
            request = RecommendationRequest(args.subject_kind, uid, args.limit, args.mode, DATASET, SPLIT)
            from .validation import check_request
            check_request(request)
            results = []
            for algorithm in ([Algorithm(args.algorithm)] if args.algorithm else list(Algorithm)):
                try:
                    value = recommend(algorithm, request)
                    results.append({"algorithm": algorithm.value, "status": "validated_provider_output", "items_count": len(value.items), "model_version": value.model_version})
                except AlgorithmNotImplemented:
                    results.append({"algorithm": algorithm.value, "status": "not_implemented", "http_equivalent": 501, "items_generated": 0})
                if args.http_url:
                    from urllib.parse import urlsplit
                    import httpx
                    origin = urlsplit(args.http_url)
                    if origin.scheme not in {"http", "https"} or not origin.hostname or origin.path or origin.query or origin.fragment or origin.username:
                        raise ContractError("HTTP_TARGET_INVALID", "http-url只接受准确服务源，不接受路径或凭据")
                    try:
                        response = httpx.get(args.http_url + f"/api/v1/demo/history-users/{uid}/recommendations/{algorithm.value}", timeout=15)
                    except httpx.HTTPError:
                        raise ContractError("HTTP_CHECK_FAILED", "无法访问实际HTTP服务") from None
                    if algorithm not in PROVIDERS:
                        body = response.json()
                        if response.status_code != 501 or body["error"]["code"] != "ALGORITHM_NOT_IMPLEMENTED" or body["error"]["details"]["items"] != []:
                            raise ContractError("HTTP_CHECK_FAILED", "真实HTTP未保持501未实现契约")
                        results[-1]["real_http_status"] = response.status_code
                    else:
                        raise ContractError("HTTP_CHECK_FAILED", "http-url选项专用于未实现501验收；已实现结果使用DTO校验")
            result = {"status": "selfcheck_passed", "subject_key": f"{args.subject_kind}:{uid}", "algorithms": results}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ContractError, ValidationError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "failed", "code": getattr(exc, "code", "CONTRACT_INVALID"),
            "message": str(exc) if isinstance(exc, ContractError) else type(exc).__name__}, ensure_ascii=False), file=sys.stderr)
        return 1
    except Exception as exc:
        print(json.dumps({"status": "failed", "code": "PROVIDER_EXECUTION_FAILED",
            "message": type(exc).__name__}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__": raise SystemExit(main())
